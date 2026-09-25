"""Dell workbench tests: real template jobs in temporary folders, no network."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from homelab import workbench as wb_module
from homelab.config import Settings
from homelab.workbench import JobCreate, ProjectCreate, Workbench

FINISHED = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}


def settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(role="dell", state_dir=tmp_path / "state", models_dir=tmp_path / "models",
                  projects_dir=tmp_path / "projects", config_dir=tmp_path / "config",
                  token="t" * 40)
    values.update(overrides)
    values["config_dir"].mkdir(parents=True, exist_ok=True)
    return Settings(**values)


@pytest.fixture
def bench(tmp_path):
    workbench = Workbench(settings(tmp_path))
    yield workbench
    if workbench._thread:
        workbench.stop()


def wait_for(workbench, job_id, states=FINISHED, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        job = workbench.get_job(job_id)
        if job["state"] in states:
            return job
        assert time.monotonic() < deadline, job
        time.sleep(0.05)


def http_status(call, *args):
    with pytest.raises(HTTPException) as caught:
        call(*args)
    return caught.value.status_code


def symlink_or_skip(link: Path, target: Path, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("Symlink creation is not permitted on this host")


# ---- Projects ---------------------------------------------------------------------

def test_python_template_project(bench, tmp_path):
    project = bench.create_project(ProjectCreate(name="demo-app", template="python"))
    assert project["id"] == "demo-app" and project["source"] == "template:python"
    assert project["presets"] == ["compile", "test"]
    root = tmp_path / "projects" / "demo-app"
    assert {p.name for p in root.iterdir()} == {"app.py", "tests", "README.md", ".gitignore", "pyproject.toml"}
    assert [item["id"] for item in bench.list_projects()["items"]] == ["demo-app"]
    assert not [p for p in (tmp_path / "projects").iterdir() if p.name.startswith(".pending-")]
    assert http_status(bench.create_project, ProjectCreate(name="demo-app", template="python")) == 409


@pytest.mark.parametrize("request_values, status", [
    ({"name": "Upper", "template": "python"}, 400),
    ({"name": "-lead", "template": "python"}, 400),
    ({"name": "has_underscore", "template": "python"}, 400),
    ({"name": "neither"}, 400),
    ({"name": "both", "template": "python", "git_url": "https://github.com/a/b"}, 400),
])
def test_project_request_validation(bench, request_values, status):
    assert http_status(bench.create_project, ProjectCreate(**request_values)) == status


def test_existing_unregistered_directory_is_not_adopted(bench, tmp_path):
    (tmp_path / "projects" / "taken").mkdir()
    assert http_status(bench.create_project, ProjectCreate(name="taken", template="python")) == 409
    assert http_status(bench.get_project, "taken") == 404


@pytest.mark.parametrize("url", [
    "http://github.com/owner/repo",
    "https://user:token@github.com/owner/repo",
    "https://token@github.com/owner/repo",
    "https://github.com/owner/repo/tree/main",
    "https://github.com/owner/repo?x=1",
    "https://github.com/owner/repo#frag",
    "https://github.com:443/owner/repo",
    "https://gitlab.com/owner/repo",
    "https://github.com.evil.example/owner/repo",
    "git@github.com:owner/repo.git",
    "ssh://git@github.com/owner/repo",
    "file:///etc/passwd",
    "https://github.com/owner",
    "https://github.com/-owner/repo",
    "https://github.com/owner/.repo",
    "https://github.com/owner/repo\n--upload-pack=touch",
    "--upload-pack=touch /tmp/x",
])
def test_github_import_rejects_non_public_https_urls(bench, monkeypatch, url):
    monkeypatch.setattr(wb_module.subprocess, "run", lambda *a, **k: pytest.fail("git must not run"))
    assert http_status(bench.create_project, ProjectCreate(name="imported", git_url=url)) == 400


def test_github_import_requires_network_permission(tmp_path, monkeypatch):
    workbench = Workbench(settings(tmp_path, allow_network_downloads=False))
    monkeypatch.setattr(wb_module.subprocess, "run", lambda *a, **k: pytest.fail("git must not run"))
    request = ProjectCreate(name="imported", git_url="https://github.com/owner/repo")
    assert http_status(workbench.create_project, request) == 403


def fake_git(monkeypatch, populate=None, returncode=0):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        destination = Path(args[-1])
        if returncode == 0:
            destination.mkdir()
            (destination / ".git").mkdir()
            (destination / "main.py").write_text("print('imported')\n", encoding="utf-8")
            if populate:
                populate(destination)
        return subprocess.CompletedProcess(args, returncode, b"", b"fatal: detail with https://secret@host")

    monkeypatch.setattr(wb_module.subprocess, "run", run)
    return calls


def test_github_import_uses_hardened_shallow_clone(bench, monkeypatch, tmp_path):
    calls = fake_git(monkeypatch)
    project = bench.create_project(ProjectCreate(name="imported", git_url="https://github.com/Owner-1/my.repo.git"))
    assert project["source"] == "https://github.com/Owner-1/my.repo.git"
    args, kwargs = calls[0]
    assert args[0] == "git"
    for option in ("protocol.file.allow=never", "protocol.ext.allow=never", "--depth", "--no-recurse-submodules"):
        assert option in args
    assert args[args.index("--") + 1] == "https://github.com/Owner-1/my.repo.git"
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["timeout"] == 90
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert not any(key.startswith(("HF_", "HOMELAB")) or "TOKEN" in key for key in kwargs["env"])
    assert Path(args[-1]).parent == tmp_path / "projects" and Path(args[-1]).name.startswith(".pending-imported-")
    assert (tmp_path / "projects" / "imported" / "main.py").is_file()


def test_github_import_failure_leaves_nothing_and_hides_git_output(bench, monkeypatch, tmp_path):
    fake_git(monkeypatch, returncode=128)
    with pytest.raises(HTTPException) as caught:
        bench.create_project(ProjectCreate(name="imported", git_url="https://github.com/owner/repo"))
    assert caught.value.status_code == 400 and "secret" not in caught.value.detail
    assert list((tmp_path / "projects").iterdir()) == []
    assert bench.list_projects()["items"] == []


def test_github_import_timeout_and_missing_git(bench, monkeypatch):
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 90)

    monkeypatch.setattr(wb_module.subprocess, "run", timeout)
    request = ProjectCreate(name="imported", git_url="https://github.com/owner/repo")
    assert http_status(bench.create_project, request) == 504

    def missing(args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(wb_module.subprocess, "run", missing)
    assert http_status(bench.create_project, request) == 503


def test_github_import_with_symlink_is_rejected(bench, monkeypatch, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    probe = tmp_path / "probe"
    symlink_or_skip(probe, outside)
    fake_git(monkeypatch, populate=lambda root: (root / "escape.txt").symlink_to(outside))
    request = ProjectCreate(name="imported", git_url="https://github.com/owner/repo")
    assert http_status(bench.create_project, request) == 400
    assert not [p for p in (tmp_path / "projects").iterdir()]


def test_github_import_entry_limit(bench, monkeypatch):
    monkeypatch.setattr(wb_module, "MAX_TREE_ENTRIES", 5)
    fake_git(monkeypatch, populate=lambda root: [(root / f"f{i}.py").write_text("") for i in range(10)])
    request = ProjectCreate(name="imported", git_url="https://github.com/owner/repo")
    assert http_status(bench.create_project, request) == 413


# ---- Jobs -------------------------------------------------------------------------

def test_template_test_and_compile_jobs_run_real_python(bench, tmp_path):
    bench.create_project(ProjectCreate(name="demo", template="python"))
    bench.start()
    test_job = bench.create_job(JobCreate(project_id="demo", preset="test"))
    assert test_job["state"] in {"queued", "running"}
    assert "args_json" not in test_job and "cancel_requested" not in test_job
    compile_job = bench.create_job(JobCreate(project_id="demo", preset="compile"))
    done = wait_for(bench, test_job["id"])
    assert done["state"] == "succeeded", done["log"]
    assert done["exit_code"] == 0
    assert "Ran 1 test" in done["log"] and "OK" in done["log"]
    assert done["started_at"] and done["finished_at"] and done["log_truncated"] is False
    assert wait_for(bench, compile_job["id"])["state"] == "succeeded"
    listed = bench.list_jobs()["items"]
    assert {job["id"] for job in listed} == {test_job["id"], compile_job["id"]}


def test_failing_project_test_reports_failure(bench, tmp_path):
    bench.create_project(ProjectCreate(name="demo", template="python"))
    app = tmp_path / "projects" / "demo" / "app.py"
    app.write_text(app.read_text(encoding="utf-8").replace("Hello", "Bye"), encoding="utf-8")
    bench.start()
    job = wait_for(bench, bench.create_job(JobCreate(project_id="demo", preset="test"))["id"])
    assert job["state"] == "failed" and job["exit_code"] == 1
    assert "FAILED (failures=1)" in job["log"]


def test_job_request_validation(bench):
    assert http_status(bench.create_job, JobCreate(project_id="missing", preset="test")) == 404
    bench.create_project(ProjectCreate(name="demo", template="python"))
    assert http_status(bench.create_job, JobCreate(project_id="demo", preset="shell")) == 400
    assert http_status(bench.get_job, "nope") == 404
    assert http_status(bench.cancel_job, "nope") == 404
    with pytest.raises(ValueError):
        JobCreate(project_id="demo", preset="test", command="rm -rf /")


def test_queue_limit(bench):
    bench.create_project(ProjectCreate(name="demo", template="python"))
    for _ in range(20):
        bench.create_job(JobCreate(project_id="demo", preset="test"))
    assert http_status(bench.create_job, JobCreate(project_id="demo", preset="test")) == 429


def add_preset(workbench, name, code, timeout=120):
    workbench.presets[name] = {"args": ["-c", code], "timeout_seconds": timeout}


def test_cancel_running_job_kills_process(bench, tmp_path):
    bench.create_project(ProjectCreate(name="demo", template="python"))
    marker = tmp_path / "projects" / "demo" / "finished.txt"
    add_preset(bench, "sleep", "import time; print('started', flush=True); time.sleep(30); open('finished.txt','w').write('x')")
    bench.start()
    job = bench.create_job(JobCreate(project_id="demo", preset="sleep"))
    deadline = time.monotonic() + 30
    while "started" not in bench.get_job(job["id"])["log"]:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert bench.get_job(job["id"])["state"] == "running"
    begin = time.monotonic()
    bench.cancel_job(job["id"])
    done = wait_for(bench, job["id"], timeout=20)
    assert done["state"] == "cancelled"
    assert time.monotonic() - begin < 15
    time.sleep(0.5)
    assert not marker.exists()


def test_cancel_queued_job_never_runs(bench, tmp_path):
    bench.create_project(ProjectCreate(name="demo", template="python"))
    add_preset(bench, "touch", "open('ran.txt','w').write('x')")
    job = bench.create_job(JobCreate(project_id="demo", preset="touch"))
    assert bench.cancel_job(job["id"])["state"] == "cancelled"
    follow_up = bench.create_job(JobCreate(project_id="demo", preset="test"))
    bench.start()
    assert wait_for(bench, follow_up["id"])["state"] == "succeeded"
    assert bench.get_job(job["id"])["state"] == "cancelled"
    assert not (tmp_path / "projects" / "demo" / "ran.txt").exists()
    # Cancelling a finished job is a no-op.
    assert bench.cancel_job(follow_up["id"])["state"] == "succeeded"


def test_job_timeout(bench):
    bench.create_project(ProjectCreate(name="demo", template="python"))
    add_preset(bench, "hang", "import time; time.sleep(30)", timeout=1)
    bench.start()
    job = wait_for(bench, bench.create_job(JobCreate(project_id="demo", preset="hang"))["id"], timeout=20)
    assert job["state"] == "timed_out"


def test_job_log_is_bounded(bench):
    bench.create_project(ProjectCreate(name="demo", template="python"))
    add_preset(bench, "noisy", "import sys\nfor i in range(4000): sys.stdout.write(f'line {i:05d} ' + 'x' * 60 + '\\n')\nprint('THE END')")
    bench.start()
    job = wait_for(bench, bench.create_job(JobCreate(project_id="demo", preset="noisy"))["id"])
    assert job["state"] == "succeeded"
    assert job["log_truncated"] is True
    assert len(job["log"].encode("utf-8")) <= wb_module.MAX_LOG_BYTES
    assert job["log"].rstrip().endswith("THE END") and "line 00000 " not in job["log"]


def test_job_environment_excludes_service_secrets(bench, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_should_not_leak")
    monkeypatch.setenv("HOMELAB_TOKEN", "should_not_leak")
    bench.create_project(ProjectCreate(name="demo", template="python"))
    add_preset(bench, "env", "import os; print(sorted(os.environ))")
    bench.start()
    job = wait_for(bench, bench.create_job(JobCreate(project_id="demo", preset="env"))["id"])
    assert job["state"] == "succeeded"
    assert "HF_TOKEN" not in job["log"] and "HOMELAB_TOKEN" not in job["log"]


def test_restart_marks_running_interrupted_and_resumes_queue(tmp_path):
    config = settings(tmp_path)
    first = Workbench(config)
    first.create_project(ProjectCreate(name="demo", template="python"))
    stale = first.create_job(JobCreate(project_id="demo", preset="test"))
    queued = first.create_job(JobCreate(project_id="demo", preset="test"))
    with first.db() as db:  # Simulates a service crash mid-job.
        db.execute("UPDATE jobs SET state='running', started_at=? WHERE id=?", (wb_module.utcnow(), stale["id"]))

    second = Workbench(config)
    second.start()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            Workbench(config).start()
        assert second.get_job(stale["id"])["state"] == "interrupted"
        assert wait_for(second, queued["id"])["state"] == "succeeded"
    finally:
        second.stop()
    # The single-worker lock is released on stop, so a restarted service can run.
    third = Workbench(config)
    third.start()
    third.stop()


def test_stop_interrupts_running_job(tmp_path):
    workbench = Workbench(settings(tmp_path))
    workbench.create_project(ProjectCreate(name="demo", template="python"))
    add_preset(workbench, "sleep", "import time; print('started', flush=True); time.sleep(30)")
    workbench.start()
    job = workbench.create_job(JobCreate(project_id="demo", preset="sleep"))
    wait_for(workbench, job["id"], states={"running"})
    workbench.stop()
    assert workbench.get_job(job["id"])["state"] == "interrupted"


def test_preset_config_is_validated(tmp_path):
    config = settings(tmp_path)
    path = config.config_dir / "workbench.json"
    path.write_text(json.dumps({"presets": {"lint": {"args": ["-m", "compileall", "."], "timeout_seconds": 60}}}), encoding="utf-8")
    if os.name == "posix" and os.geteuid() != 0:
        with pytest.raises(ValueError, match="root owned"):
            Workbench(config)
        return
    assert "lint" in Workbench(config).presets
    for bad in ({"presets": {"x": {"args": "rm -rf /", "timeout_seconds": 5}}},
                {"presets": {"x": {"args": ["-c", "1"], "timeout_seconds": 0}}},
                {"presets": {"x": {"args": ["-c", "1"], "timeout_seconds": 5, "shell": True}}},
                {"presets": {"Bad Name": {"args": ["-c", "1"], "timeout_seconds": 5}}},
                {"presets": {}, "commands": []}):
        path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError):
            Workbench(config)


# ---- Search and file reads --------------------------------------------------------

@pytest.fixture
def indexed(bench, tmp_path):
    for name in ("alpha", "beta"):
        bench.create_project(ProjectCreate(name=name, template="python"))
    alpha = tmp_path / "projects" / "alpha"
    (alpha / "notes.md").write_bytes(b"The zebracorn protocol lives here.\n")
    (alpha / ".env").write_text("API_KEY=zebracorn-secret\n", encoding="utf-8")
    (alpha / "server.pem").write_text("zebracorn private key\n", encoding="utf-8")
    (alpha / "node_modules").mkdir()
    (alpha / "node_modules" / "lib.js").write_text("zebracorn dependency\n", encoding="utf-8")
    (alpha / ".git").mkdir()
    (alpha / ".git" / "config").write_text("zebracorn git\n", encoding="utf-8")
    (alpha / "blob.bin").write_bytes(b"zebracorn\x00binary")
    (alpha / "big.txt").write_text("zebracorn " * 40000, encoding="utf-8")
    (alpha / "latin1.txt").write_bytes("zebracorn caf\xe9".encode("latin-1"))
    (tmp_path / "projects" / "beta" / "other.py").write_text("# zebracorn in beta\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("zebracorn outside the projects\n", encoding="utf-8")
    return bench


def test_search_indexes_only_safe_project_text(indexed, tmp_path):
    result = indexed.reindex()
    # Five template files per project, plus notes.md and other.py. Secrets,
    # dependencies, Git data, binary, oversized, and non-UTF-8 files are skipped.
    assert result["indexed_files"] == 12
    hits = indexed.search("zebracorn")["items"]
    assert sorted((hit["project_id"], hit["path"]) for hit in hits) == [("alpha", "notes.md"), ("beta", "other.py")]
    assert "[zebracorn]" in next(hit["snippet"] for hit in hits if hit["path"] == "notes.md")
    scoped = indexed.search("zebracorn", "beta")["items"]
    assert [(hit["project_id"], hit["path"]) for hit in scoped] == [("beta", "other.py")]
    assert indexed.search("def greet")["items"]
    assert http_status(indexed.search, "zebracorn", "missing") == 404
    assert http_status(indexed.search, "   ") == 400
    assert http_status(indexed.search, "x" * 201) == 400


@pytest.mark.parametrize("query", ['"', 'zebracorn OR x*', 'NEAR(a b)', 'path:notes.md', "'; DROP TABLE jobs; --", "!!!", "*"])
def test_search_treats_query_syntax_as_text(indexed, query):
    indexed.reindex()
    assert isinstance(indexed.search(query)["items"], list)
    assert indexed.list_jobs() == {"items": []}


def test_reindex_single_project_and_stale_results(indexed, tmp_path):
    indexed.reindex()
    assert indexed.reindex("beta")["indexed_files"] == 6
    (tmp_path / "projects" / "alpha" / "notes.md").unlink()
    # The stale row is still indexed but must not be shown.
    assert [hit["path"] for hit in indexed.search("zebracorn")["items"]] == ["other.py"]
    assert http_status(indexed.reindex, "missing") == 404


def test_search_hides_file_replaced_by_symlink(indexed, tmp_path):
    indexed.reindex()
    notes = tmp_path / "projects" / "alpha" / "notes.md"
    notes.unlink()
    symlink_or_skip(notes, tmp_path / "outside.txt")
    assert [hit["path"] for hit in indexed.search("zebracorn")["items"]] == ["other.py"]
    assert http_status(indexed.read_file, "alpha", "notes.md") == 404
    indexed.reindex()
    assert [hit["path"] for hit in indexed.search("zebracorn")["items"]] == ["other.py"]


def test_symlinked_directory_is_not_indexed_or_read(indexed, tmp_path):
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "leak.py").write_text("zebracorn leak\n", encoding="utf-8")
    symlink_or_skip(tmp_path / "projects" / "alpha" / "linked", outside_dir, directory=True)
    indexed.reindex()
    assert "linked/leak.py" not in [hit["path"] for hit in indexed.search("zebracorn")["items"]]
    assert http_status(indexed.read_file, "alpha", "linked/leak.py") == 404


def test_read_file_confinement(indexed, tmp_path):
    content = indexed.read_file("alpha", "notes.md")
    assert content == {"project_id": "alpha", "path": "notes.md", "content": "The zebracorn protocol lives here.\n", "truncated": False}
    assert indexed.read_file("alpha", "tests/test_app.py")["content"].startswith("import unittest")
    assert indexed.read_file("alpha", ".gitignore")["content"]
    cases = {
        "../outside.txt": 400, "tests/../../../outside.txt": 400, "./notes.md": 400,
        str(tmp_path / "outside.txt"): 400, "/etc/passwd": 400, "tests\\test_app.py": 400,
        "C:/Windows/win.ini": 400, "notes.md\x00": 400, "": 400, "tests//test_app.py": 400,
        ".env": 403, "server.pem": 403, "node_modules/lib.js": 403, ".git/config": 403,
        "missing.py": 404, "tests": 400, "blob.bin": 415, "latin1.txt": 415, "big.txt": 413,
    }
    for path, status in cases.items():
        assert http_status(indexed.read_file, "alpha", path) == status, path
    assert http_status(indexed.read_file, "../alpha", "notes.md") == 404
    assert http_status(indexed.read_file, "missing", "notes.md") == 404


def test_project_directory_replaced_by_symlink(indexed, tmp_path):
    beta = tmp_path / "projects" / "beta"
    import shutil
    shutil.rmtree(beta)
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "other.py").write_text("zebracorn\n", encoding="utf-8")
    symlink_or_skip(beta, outside_dir, directory=True)
    assert http_status(indexed.read_file, "beta", "other.py") == 409


# ---- HTTP API -----------------------------------------------------------------------

def test_api_routes(tmp_path):
    app = FastAPI()
    app.include_router(wb_module.create_router(settings(tmp_path)), prefix="/api")
    with TestClient(app) as client:
        created = client.post("/api/projects", json={"name": "web", "template": "python"})
        assert created.status_code == 201
        assert client.post("/api/projects", json={"name": "web", "template": "python"}).status_code == 409
        assert client.post("/api/projects", json={"name": "x", "template": "node"}).status_code == 422
        assert client.post("/api/projects", json={"name": "x", "template": "python", "path": "/"}).status_code == 422
        assert [p["id"] for p in client.get("/api/projects").json()["items"]] == ["web"]
        job = client.post("/api/jobs", json={"project_id": "web", "preset": "test"})
        assert job.status_code == 202
        assert client.post("/api/jobs", json={"project_id": "web", "preset": "test", "args": ["-c", "1"]}).status_code == 422
        job_id = job.json()["id"]
        deadline = time.monotonic() + 60
        while client.get(f"/api/jobs/{job_id}").json()["state"] not in FINISHED:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert client.get(f"/api/jobs/{job_id}").json()["state"] == "succeeded"
        assert client.post(f"/api/jobs/{job_id}/cancel").json()["state"] == "succeeded"
        assert client.get("/api/jobs").json()["items"][0]["id"] == job_id
        assert client.post("/api/search/reindex", json={}).json()["indexed_files"] == 5
        assert client.post("/api/search/reindex", json={"project_id": "web"}).status_code == 200
        hits = client.get("/api/search", params={"q": "greet", "project_id": "web"}).json()["items"]
        assert {hit["path"] for hit in hits} >= {"app.py"}
        assert client.get("/api/search", params={"q": ""}).status_code == 422
        read = client.get("/api/files", params={"project_id": "web", "path": "app.py"})
        assert read.status_code == 200 and "def greet" in read.json()["content"]
        assert client.get("/api/files", params={"project_id": "web", "path": "../web/app.py"}).status_code == 400
        assert client.get("/api/jobs/unknown").status_code == 404


def test_router_is_empty_for_hp(tmp_path):
    assert wb_module.create_router(settings(tmp_path, role="hp")).routes == []
