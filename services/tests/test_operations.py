"""Operations tests. Every repository, password and source is temporary; restic is a fake script."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import textwrap
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import psutil
import pytest

from homelab import operations as ops
from homelab.config import Settings

PASSWORD = "correct-horse-battery-staple-test-only"

FAKE_RESTIC = r'''
import json, os, sys, time
from pathlib import Path
here = Path(__file__).parent
control = json.loads((here / "control.json").read_text())
args = sys.argv[1:]
password_file = os.environ.get("RESTIC_PASSWORD_FILE", "")
with open(here / "calls.jsonl", "a", encoding="utf-8") as stream:
    stream.write(json.dumps(dict(args=args, password_file=password_file, pid=os.getpid(),
                                 password_readable=Path(password_file).is_file(),
                                 other_restic=sorted(k for k in os.environ if k.startswith("RESTIC_") and k != "RESTIC_PASSWORD_FILE"))) + "\n")
command = next(a for a in args if a in ("backup", "check", "restore", "snapshots"))
mode = control.get(command, "ok")
if command == "snapshots":
    if mode == "fail":
        print("Fatal: wrong password", file=sys.stderr); sys.exit(1)
    print(json.dumps(control.get("snapshot_list", []))); sys.exit(0)
if mode == "sleep":
    print("started", flush=True)
    time.sleep(120)
if mode == "flood":
    sys.stdout.write("x" * 200_000 + "\nEND-OF-LOG\n"); sys.exit(0)
if command == "restore":
    target = Path(args[args.index("--target") + 1])
    (target / "restored.txt").write_text("restored")
print(f"{command} done", flush=True)
sys.exit(3 if mode == "fail" else 0)
'''


def drive_relative(path: Path) -> str:
    """A '/'-rooted spelling of a temp path; on Windows it resolves on the current drive."""
    text = str(path).replace("\\", "/")
    return text[2:] if len(text) > 1 and text[1] == ":" else text


def wait_for(operations: ops.Operations, task_id: str, states=ops.TERMINAL, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = operations.task(task_id)
        if task["state"] in states:
            return task
        time.sleep(0.05)
    raise AssertionError(f"Task stayed {operations.task(task_id)['state']}")


def wait_log(operations: ops.Operations, task_id: str, text: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if text in operations.task(task_id)["log"]:
            return
        time.sleep(0.05)
    raise AssertionError(f"{text!r} never appeared in the task log")


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    (fake_dir / "restic.py").write_text(textwrap.dedent(FAKE_RESTIC), encoding="utf-8")
    (fake_dir / "control.json").write_text("{}", encoding="utf-8")
    settings = Settings(role="dell", state_dir=tmp_path / "state", models_dir=tmp_path / "models",
                        projects_dir=tmp_path / "projects", config_dir=tmp_path / "config")
    for folder in (settings.state_dir, settings.models_dir, settings.projects_dir, settings.config_dir / "backups"):
        folder.mkdir(parents=True)
    source = tmp_path / "source"
    source.mkdir()
    (source / "notes.txt").write_text("keep me", encoding="utf-8")
    mount = tmp_path / "backup-disk"
    repository = mount / "repo"
    repository.mkdir(parents=True)
    password = settings.config_dir / "secrets" / "projects.password"
    password.parent.mkdir()
    password.write_text(PASSWORD + "\n", encoding="utf-8")
    # Pretend the temp backup folder is a separately mounted filesystem.
    monkeypatch.setattr(ops, "is_mount", lambda path: Path(path) == mount)
    monkeypatch.setattr(ops, "device", lambda path: 2 if Path(path) == mount else 1)

    def control(**values):
        (fake_dir / "control.json").write_text(json.dumps(values), encoding="utf-8")

    def calls():
        path = fake_dir / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def plan(plan_id="projects", **overrides):
        data = dict(name="Projects", repository=str(repository), repository_mount=str(mount),
                    password_file=str(password), sources=[str(source)], excludes=["*.tmp"], timeout_seconds=60)
        data.update(overrides)
        data = {key: value for key, value in data.items() if value is not None}
        (settings.config_dir / "backups" / f"{plan_id}.json").write_text(json.dumps(data), encoding="utf-8")

    created = []

    def make(restic=(sys.executable, str(fake_dir / "restic.py"))):
        operations = ops.Operations(settings, list(restic) if restic else None)
        if restic is None:
            operations.restic = None
        created.append(operations)
        return operations

    yield SimpleNamespace(settings=settings, source=source, mount=mount, repository=repository, password=password,
                          control=control, calls=calls, plan=plan, make=make, fake_dir=fake_dir, tmp=tmp_path)
    for operations in created:
        operations.close()


# ---- plan validation ----

def test_permission_rules_reject_app_writable_plans_and_public_secrets():
    path = Path("/etc/homelab/app-config/backups/x.json")
    ops.check_permissions(path, SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o640))
    ops.check_permissions(path, SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o640), secret=True)
    with pytest.raises(ValueError, match="owned by root"):
        ops.check_permissions(path, SimpleNamespace(st_uid=1000, st_mode=stat.S_IFREG | 0o600))
    with pytest.raises(ValueError, match="writable"):
        ops.check_permissions(path, SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o660))
    with pytest.raises(ValueError, match="writable"):
        ops.check_permissions(path, SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o602))
    with pytest.raises(ValueError, match="readable by others"):
        ops.check_permissions(path, SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644), secret=True)
    # SSH keys may belong to another administrator account but still must be private.
    ops.check_permissions(path, SimpleNamespace(st_uid=1000, st_mode=stat.S_IFREG | 0o640), root_owned=False, secret=True)


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership checks")
def test_non_root_owned_plan_is_rejected_on_linux(env):
    env.plan()
    if os.geteuid() == 0:
        pytest.skip("running as root")
    with pytest.raises(ValueError, match="owned by root"):
        env.make().load_plan("projects")


def test_valid_local_plan_loads(env):
    env.plan()
    plan = env.make().load_plan("projects")
    assert plan.destination_type == "local"
    assert plan.sources == [str(env.source)]
    assert plan.password_file == env.password
    assert plan.timeout_seconds == 60


@pytest.mark.parametrize("change, message", [
    (dict(repository_mount="/not/mounted"), "not mounted"),
    (dict(repository="OUTSIDE"), "inside its configured backup mount"),
    (dict(repository="MOUNT"), "inside its configured backup mount"),
    (dict(password_file="MISSING"), "missing"),
    (dict(password_file="relative.password"), "absolute"),
    (dict(sources=[]), "At least one source"),
    (dict(sources=["relative/dir"]), "not absolute"),
    (dict(timeout_seconds=0), "timeout_seconds"),
    (dict(excludes="*.tmp"), "excludes"),
    (dict(repository=""), "nonempty"),
])
def test_invalid_local_plans_are_rejected(env, change, message):
    replacements = {"OUTSIDE": str(env.tmp / "elsewhere"), "MOUNT": str(env.mount),
                    "MISSING": str(env.tmp / "missing.password")}
    env.plan(**{key: replacements.get(value, value) if isinstance(value, str) else value
                for key, value in change.items()})
    with pytest.raises(ValueError, match=message):
        env.make().load_plan("projects")


def test_local_repository_on_source_filesystem_is_rejected(env, monkeypatch):
    monkeypatch.setattr(ops, "device", lambda path: 1)
    env.plan()
    with pytest.raises(ValueError, match="separate filesystem"):
        env.make().load_plan("projects")


def test_source_containing_the_backup_mount_is_rejected(env):
    env.plan(sources=[str(env.tmp)])
    with pytest.raises(ValueError, match="inside a source"):
        env.make().load_plan("projects")


@pytest.mark.parametrize("plan_id", ["../etc", "-x", "a" * 65, "", "a.b"])
def test_plan_id_cannot_escape_config_directory(env, plan_id):
    with pytest.raises(ValueError, match="Invalid backup plan ID"):
        env.make().load_plan(plan_id)


def test_sftp_plan_builds_strict_ssh_command(env):
    key = env.settings.config_dir / "secrets" / "backup_ed25519"
    known = env.settings.config_dir / "secrets" / "known_hosts"
    key.write_text("private", encoding="utf-8")
    known.write_text("host key", encoding="utf-8")
    env.plan(repository="sftp:backup@192.168.1.20:/srv/bulk/backups/dell", repository_mount=None,
             ssh_key=drive_relative(key), known_hosts=drive_relative(known))
    operations = env.make()
    plan = operations.load_plan("projects")
    assert plan.destination_type == "sftp"
    command, environment = operations._command(plan)
    option = command[command.index("-o") + 1]
    assert option.startswith("sftp.command=ssh -i ")
    for expected in ("BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes",
                     f"UserKnownHostsFile={drive_relative(known)}", "-s backup@192.168.1.20 sftp"):
        assert expected in option
    assert "'" not in option and '"' not in option  # restic splits this itself; no quoting needed.
    assert environment["RESTIC_PASSWORD_FILE"] == str(env.password)
    assert PASSWORD not in " ".join(command)


@pytest.mark.parametrize("repository", [
    "sftp:-oProxyCommand=touch@host:/dir",
    "sftp:backup@-oProxyCommand:/dir",
    "sftp:backup@host:relative",
    "sftp:host:/dir",
    "sftp:backup@host:/dir with space",
    "sftp:backup@host:/dir'quote",
])
def test_unsafe_sftp_repositories_are_rejected(env, repository):
    env.plan(repository=repository, repository_mount=None, ssh_key="/k", known_hosts="/h")
    with pytest.raises(ValueError, match="SFTP repository"):
        env.make().load_plan("projects")


def test_sftp_credential_paths_with_quotes_or_spaces_are_rejected(env):
    env.plan(repository="sftp:backup@hp:/srv/b", repository_mount=None,
             ssh_key="/etc/homelab/my key", known_hosts="/etc/homelab/known_hosts")
    with pytest.raises(ValueError, match="ssh_key"):
        env.make().load_plan("projects")


# ---- task execution ----

def test_backup_runs_restic_with_password_file_and_records_success(env, monkeypatch):
    monkeypatch.setenv("RESTIC_PASSWORD", "leaked-from-environment")
    monkeypatch.setenv("RESTIC_REPOSITORY", "/somewhere/else")
    env.plan()
    operations = env.make()
    task = operations.submit("projects", "backup")
    assert task["state"] == "queued" and task["restore_path"] is None
    done = wait_for(operations, task["id"])
    assert done["state"] == "succeeded", done
    assert done["exit_code"] == 0 and done["error"] is None
    assert done["started_at"] and done["finished_at"]
    assert "backup done" in done["log"]
    call = env.calls()[-1]
    args = call["args"]
    assert args[args.index("--repo") + 1] == str(env.repository)
    assert args[args.index("--cache-dir") + 1] == str(env.settings.state_dir / "operations" / "cache")
    assert args[args.index("backup"):] == ["backup", "--exclude", "*.tmp", "--", str(env.source)]
    assert call["password_file"] == str(env.password) and call["password_readable"]
    assert call["other_restic"] == []  # Inherited RESTIC_* variables cannot redirect the job.
    stored = (env.settings.state_dir / "operations" / "tasks" / f"{task['id']}.json").read_text(encoding="utf-8")
    assert PASSWORD not in stored and PASSWORD not in " ".join(args)


def test_nonzero_exit_is_failure(env):
    env.plan()
    env.control(backup="fail")
    operations = env.make()
    done = wait_for(operations, operations.submit("projects", "backup")["id"])
    assert done["state"] == "failed"
    assert done["exit_code"] == 3
    assert "status 3" in done["error"]


def test_missing_executable_fails_task_without_crashing(env):
    env.plan()
    operations = env.make(restic=[str(env.tmp / "no-such-restic")])
    done = wait_for(operations, operations.submit("projects", "check")["id"])
    assert done["state"] == "failed"
    assert "FileNotFoundError" in done["error"]
    assert operations.workers == {} and operations.cancel_events == {}


def test_check_reads_all_data(env):
    env.plan()
    operations = env.make()
    done = wait_for(operations, operations.submit("projects", "check")["id"])
    assert done["state"] == "succeeded"
    assert env.calls()[-1]["args"][-2:] == ["check", "--read-data"]


def test_cancel_stops_process_and_records_cancelled(env):
    env.plan()
    env.control(backup="sleep")
    operations = env.make()
    task = operations.submit("projects", "backup")
    wait_log(operations, task["id"], "started")
    assert operations.task(task["id"])["state"] == "running"
    pid = env.calls()[-1]["pid"]
    operations.cancel(task["id"])
    done = wait_for(operations, task["id"], timeout=20)
    assert done["state"] == "cancelled"
    assert done["error"] == "Cancelled by the user."
    assert "started" in done["log"]
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    # A finished task can be "cancelled" again harmlessly.
    assert operations.cancel(task["id"])["state"] == "cancelled"


def test_time_limit_terminates_and_fails(env):
    env.plan(timeout_seconds=1)
    env.control(backup="sleep")
    operations = env.make()
    done = wait_for(operations, operations.submit("projects", "backup")["id"], timeout=20)
    assert done["state"] == "failed"
    assert "1-second time limit" in done["error"]


def test_shutdown_marks_running_task_interrupted_not_cancelled(env):
    env.plan()
    env.control(check="sleep")
    operations = env.make()
    task = operations.submit("projects", "check")
    wait_log(operations, task["id"], "started")
    operations.close()
    done = operations.task(task["id"])
    assert done["state"] == "interrupted"
    assert "service stopped" in done["error"]


def test_only_one_operation_runs_at_a_time(env):
    env.plan()
    env.control(backup="sleep")
    operations = env.make()
    task = operations.submit("projects", "backup")
    with pytest.raises(RuntimeError, match="already running"):
        operations.submit("projects", "check")
    operations.cancel(task["id"])
    wait_for(operations, task["id"])
    second = operations.submit("projects", "check")
    assert wait_for(operations, second["id"])["state"] == "succeeded"


def test_log_is_bounded_and_keeps_the_tail(env):
    env.plan()
    env.control(backup="flood")
    operations = env.make()
    done = wait_for(operations, operations.submit("projects", "backup")["id"])
    assert done["state"] == "succeeded"
    assert len(done["log"].encode("utf-8")) <= ops.MAX_LOG
    assert done["log"].rstrip().endswith("END-OF-LOG")


def test_restore_goes_to_a_new_folder_under_state_restores(env):
    env.plan()
    operations = env.make()
    snapshot = "a" * 64
    first = operations.submit("projects", "restore", snapshot)
    done = wait_for(operations, first["id"])
    assert done["state"] == "succeeded"
    target = Path(done["restore_path"])
    assert target.parent == env.settings.state_dir / "restores"
    assert target.name == first["id"]
    assert (target / "restored.txt").read_text() == "restored"
    args = env.calls()[-1]["args"]
    assert args[args.index("restore"):] == ["restore", snapshot, "--target", str(target), "--verify"]
    second = wait_for(operations, operations.submit("projects", "restore", snapshot)["id"])
    assert second["restore_path"] != done["restore_path"]
    assert not (env.source / "restored.txt").exists()


@pytest.mark.parametrize("snapshot", ["", "latest", "abc", "--target=/", "g" * 64, "a" * 65])
def test_restore_requires_a_specific_snapshot_id(env, snapshot):
    env.plan()
    operations = env.make()
    with pytest.raises(ValueError, match="snapshot ID"):
        operations.submit("projects", "restore", snapshot)
    assert not (env.settings.state_dir / "restores").exists()


def test_snapshots_are_listed_and_failures_hidden(env):
    env.plan()
    env.control(snapshot_list=[{"id": "b" * 64, "short_id": "bbbbbbbb", "time": "2026-09-01T00:00:00Z",
                                "hostname": "dell", "paths": ["/srv/projects"], "tree": "ignored"}])
    operations = env.make()
    items = operations.snapshots("projects")
    assert items == [{"id": "b" * 64, "short_id": "bbbbbbbb", "time": "2026-09-01T00:00:00Z",
                      "hostname": "dell", "paths": ["/srv/projects"]}]
    env.control(snapshots="fail")
    with pytest.raises(ValueError, match="Cannot read snapshots"):
        operations.snapshots("projects")


# ---- restart state and metrics ----

def test_restart_marks_unfinished_tasks_interrupted(env):
    tasks_dir = env.settings.state_dir / "operations" / "tasks"
    tasks_dir.mkdir(parents=True)
    for state in ("queued", "running", "succeeded"):
        task_id = (state[0] * 32)[:32].replace("q", "a").replace("r", "b").replace("s", "c")
        (tasks_dir / f"{task_id}.json").write_text(json.dumps(dict(
            id=task_id, plan_id="projects", action="backup", state=state, created_at="2026-01-01T00:00:00+00:00",
            log="")), encoding="utf-8")
    (tasks_dir / f"{'d' * 32}.json").write_text("{corrupt", encoding="utf-8")
    operations = env.make()
    states = {task["id"]: task["state"] for task in operations.tasks()}
    assert states == {"a" * 32: "interrupted", "b" * 32: "interrupted", "c" * 32: "succeeded"}
    assert "Inspect the repository" in operations.task("b" * 32)["error"]
    # An interrupted task no longer blocks new work.
    env.plan()
    assert wait_for(operations, operations.submit("projects", "check")["id"])["state"] == "succeeded"


def test_old_tasks_are_pruned(env, monkeypatch):
    monkeypatch.setattr(ops, "MAX_TASKS", 3)
    tasks_dir = env.settings.state_dir / "operations" / "tasks"
    tasks_dir.mkdir(parents=True)
    for index in range(5):
        path = tasks_dir / f"{index:032x}.json"
        path.write_text(json.dumps(dict(id=f"{index:032x}", state="succeeded", created_at=str(index))), encoding="utf-8")
        os.utime(path, (1000 + index, 1000 + index))
    operations = env.make()
    assert [task["created_at"] for task in operations.tasks()] == ["4", "3", "2"]


def test_metrics_are_real_and_history_survives_restart(env):
    operations = env.make()
    data = operations.metrics()
    assert data["host"]["role"] == "dell"
    current = data["current"]
    assert 0 <= current["cpu_percent"] <= 100
    assert current["memory"]["total"] == psutil.virtual_memory().total
    assert set(current["memory"]) == {"total", "available", "used", "percent"}
    assert {disk["path"] for disk in current["disks"]} >= {str(env.settings.state_dir.resolve())}
    assert all({"total", "used", "free", "percent"} <= set(disk) for disk in current["disks"])
    assert all({"name", "bytes_sent", "bytes_recv", "errin", "errout", "is_up", "speed_mbps"} <= set(n)
               for n in current["network"])
    assert len(data["history"]) == 1
    # Within five seconds the cached sample is reused rather than appending a duplicate.
    assert len(operations.metrics()["history"]) == 1
    operations.last_sample -= 5
    assert len(operations.metrics()["history"]) == 2
    reloaded = env.make()
    assert len(reloaded.history) == 2
    assert reloaded.history[0]["timestamp"] == data["history"][0]["timestamp"]


def test_corrupt_or_oversized_history_is_handled(env):
    history = env.settings.state_dir / "operations" / "metrics.json"
    history.parent.mkdir(parents=True)
    history.write_text("not json", encoding="utf-8")
    assert env.make().history == []
    history.write_text(json.dumps([{"timestamp": str(i)} for i in range(ops.MAX_HISTORY + 50)] + ["junk"]), encoding="utf-8")
    loaded = env.make().history
    assert len(loaded) == ops.MAX_HISTORY and loaded[-1]["timestamp"] == str(ops.MAX_HISTORY + 49)


# ---- HTTP API contract ----

@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(ops.create_router(env.settings, [sys.executable, str(env.fake_dir / "restic.py")]), prefix="/api")
    with TestClient(app) as test_client:  # Runs the router lifespan: collector start and clean shutdown.
        yield test_client


def api_wait(client, task_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/api/backups/tasks/{task_id}").json()
        if task["state"] in ops.TERMINAL:
            return task
        time.sleep(0.05)
    raise AssertionError("task did not finish")


def test_api_routes_match_contract(client):
    schema = client.get("/openapi.json").json()
    routes = {(path, method.upper()) for path, item in schema["paths"].items() for method in item}
    for expected in [("/api/metrics", "GET"), ("/api/backups", "GET"), ("/api/backups/run", "POST"),
                     ("/api/backups/check", "POST"), ("/api/backups/restore", "POST"),
                     ("/api/backups/tasks/{task_id}", "GET")]:
        assert expected in routes
    # The API offers no way to create or edit plans, destinations or commands.
    assert not any(method in ("PUT", "PATCH", "DELETE") for _, method in routes)
    components = schema["components"]["schemas"]
    assert set(components["PlanRequest"]["properties"]) == {"plan_id"}
    assert set(components["RestoreRequest"]["properties"]) == {"plan_id", "snapshot_id"}
    for path in ("/api/backups/run", "/api/backups/check", "/api/backups/restore"):
        assert "202" in schema["paths"][path]["post"]["responses"]


def test_api_distinguishes_no_plan_from_success(client, env):
    empty = client.get("/api/backups").json()
    assert empty == {"available": True, "plans": [], "tasks": []}
    env.plan()
    listed = client.get("/api/backups").json()["plans"]
    assert listed[0]["configured"] and listed[0]["last_backup"] is None
    response = client.post("/api/backups/run", json={"plan_id": "projects"})
    assert response.status_code == 202
    task = api_wait(client, response.json()["id"])
    assert task["state"] == "succeeded"
    body = client.get("/api/backups").json()
    assert body["plans"][0]["last_backup"] == {"id": task["id"], "state": "succeeded", "finished_at": task["finished_at"]}
    assert "log" not in body["tasks"][0]
    assert PASSWORD not in json.dumps(body) and PASSWORD not in json.dumps(task)


def test_api_reports_broken_plan_without_hiding_it(client, env):
    env.plan(password_file=str(env.tmp / "gone.password"))
    plan = client.get("/api/backups").json()["plans"][0]
    assert plan["configured"] is False and "missing" in plan["error"]
    assert client.post("/api/backups/run", json={"plan_id": "projects"}).status_code == 400


def test_api_errors(client, env):
    env.plan()
    assert client.post("/api/backups/run", json={}).status_code == 422
    assert client.post("/api/backups/run", json={"plan_id": "../x"}).status_code == 400
    assert client.post("/api/backups/check", json={"plan_id": "nope"}).status_code == 400
    assert client.post("/api/backups/restore", json={"plan_id": "projects", "snapshot_id": "short"}).status_code == 422
    assert client.post("/api/backups/restore", json={"plan_id": "projects", "snapshot_id": "latest!!"}).status_code == 400
    assert client.get("/api/backups/tasks/" + "0" * 32).status_code == 404
    assert client.get("/api/backups/tasks/..%2Fx").status_code == 404
    assert client.post("/api/backups/tasks/" + "0" * 32 + "/cancel").status_code == 404


def test_api_conflict_cancel_restore_and_snapshots(client, env):
    env.plan()
    env.control(backup="sleep", snapshot_list=[{"id": "c" * 64, "short_id": "cccccccc"}])
    running = client.post("/api/backups/run", json={"plan_id": "projects"}).json()
    assert client.post("/api/backups/check", json={"plan_id": "projects"}).status_code == 409
    assert client.post(f"/api/backups/tasks/{running['id']}/cancel").status_code == 200
    assert api_wait(client, running["id"])["state"] == "cancelled"
    snapshots = client.get("/api/backups/projects/snapshots").json()["items"]
    assert snapshots[0]["id"] == "c" * 64
    restore = client.post("/api/backups/restore", json={"plan_id": "projects", "snapshot_id": snapshots[0]["id"]})
    assert restore.status_code == 202
    done = api_wait(client, restore.json()["id"])
    assert done["state"] == "succeeded"
    assert Path(done["restore_path"]).parent == env.settings.state_dir / "restores"


def test_api_metrics(client):
    data = client.get("/api/metrics").json()
    assert set(data) == {"host", "current", "history"}
    assert {"hostname", "role", "platform", "boot_time"} <= set(data["host"])
    assert {"timestamp", "cpu_percent", "memory", "load_average", "disks", "network", "disk_io"} <= set(data["current"])
    assert data["history"] and {"timestamp", "cpu_percent", "memory_percent",
                                "network_bytes_sent", "network_bytes_recv"} <= set(data["history"][-1])


def test_api_without_restic_reports_unavailable(env):
    env.plan()
    app = FastAPI()
    router = ops.create_router(env.settings, [])
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        assert client.get("/api/backups").json()["available"] is False
        response = client.post("/api/backups/run", json={"plan_id": "projects"})
        assert response.status_code == 400 and "restic is not installed" in response.json()["detail"]
