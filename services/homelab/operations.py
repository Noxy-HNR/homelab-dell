"""Real host measurements and constrained, persistent restic operations."""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import threading
import time
from typing import Literal
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import psutil

from .config import Settings

MAX_LOG = 64 * 1024
MAX_TASKS = 100
MAX_HISTORY = 720  # One hour at the normal five-second sample interval.
LOG_FLUSH_SECONDS = 1.0
PLAN_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
# User and host must start with a letter or digit so neither can be read by ssh as an option.
SFTP_REPOSITORY = re.compile(r"sftp:[A-Za-z0-9][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*:/[^\s\x00'\"\\]+")
# Credential paths are embedded in restic's sftp.command, so keep them free of quoting characters.
SAFE_PATH = re.compile(r"/[A-Za-z0-9_./@+=,-]+")
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
SHUTDOWN = "The service stopped while this task was running. Inspect the repository before retrying."


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def check_permissions(path: Path, info: os.stat_result, *, root_owned: bool = True, secret: bool = False) -> None:
    if root_owned and info.st_uid != 0:
        raise ValueError(f"File must be owned by root: {path}")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError(f"File must not be writable by group or others: {path}")
    if secret and info.st_mode & stat.S_IROTH:
        raise ValueError(f"Credential file must not be readable by others: {path}")


def protected_file(path: Path, *, root_owned: bool = True, secret: bool = False) -> None:
    """Reject app-writable plans and publicly readable credential files on Linux."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Required regular file is missing: {path}")
    if os.name == "posix":
        check_permissions(path, path.stat(), root_owned=root_owned, secret=secret)
    if secret and not os.access(path, os.R_OK):
        raise ValueError(f"The homelab service account cannot read: {path}")


def is_mount(path: Path) -> bool:
    return os.path.ismount(path)


def device(path: Path) -> int:
    return path.stat().st_dev


@dataclass(frozen=True)
class BackupPlan:
    id: str
    name: str
    repository: str
    password_file: Path
    sources: list[str]
    excludes: list[str]
    timeout_seconds: int
    destination_type: str
    repository_mount: Path | None = None
    ssh_key: Path | None = None
    known_hosts: Path | None = None


class Operations:
    def __init__(self, settings: Settings, restic: str | Sequence[str] | None = None):
        self.settings = settings
        self.root = settings.state_dir / "operations"
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks_dir = self.root / "tasks"
        self.tasks_dir.mkdir(exist_ok=True)
        self.history_path = self.root / "metrics.json"
        # A command prefix; tests may use [python, fake_restic.py].
        if restic is None:
            found = shutil.which("restic")
            self.restic = [found] if found else None
        else:
            self.restic = [restic] if isinstance(restic, str) else list(restic)
        self.lock = threading.RLock()
        self.cancel_events: dict[str, threading.Event] = {}
        self.cancel_reasons: dict[str, tuple[str, str]] = {}
        self.workers: dict[str, threading.Thread] = {}
        self.last_sample = 0.0
        self.current: dict = {}
        try:
            value = json.loads(self.history_path.read_text(encoding="utf-8"))
            self.history = [item for item in value if isinstance(item, dict)][-MAX_HISTORY:] if isinstance(value, list) else []
        except (OSError, ValueError):
            self.history = []
        # No web restart can make an unfinished backup appear successful.
        for path in self.tasks_dir.glob("*.json"):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
                if task.get("state") not in TERMINAL:
                    task.update(state="interrupted", finished_at=now(),
                                error="The service stopped before this task finished. Inspect the repository before retrying.")
                    atomic_json(path, task)
            except (OSError, ValueError, AttributeError):
                continue
        self._prune_tasks()
        psutil.cpu_percent(interval=None)  # Establish the first measurement interval.

    def _prune_tasks(self) -> None:
        def modified(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0
        paths = sorted(self.tasks_dir.glob("*.json"), key=modified, reverse=True)
        for path in paths[MAX_TASKS:]:
            path.unlink(missing_ok=True)

    def metrics(self) -> dict:
        with self.lock:
            if time.monotonic() - self.last_sample >= 5 or not self.current:
                memory = psutil.virtual_memory()
                stats = psutil.net_if_stats()
                counters = psutil.net_io_counters(pernic=True)
                network = []
                for name, counter in counters.items():
                    info = stats.get(name)
                    network.append(dict(name=name, **counter._asdict(),
                                        is_up=info.isup if info else None,
                                        speed_mbps=info.speed if info and info.speed > 0 else None))
                disks = []
                paths = [Path("/"), self.settings.state_dir, self.settings.models_dir, self.settings.projects_dir]
                seen = set()
                for path in paths:
                    resolved = str(path.resolve())
                    if resolved in seen or not path.exists():
                        continue
                    seen.add(resolved)
                    try:
                        disks.append(dict(path=resolved, **psutil.disk_usage(resolved)._asdict()))
                    except OSError:
                        continue
                try:
                    disk_io = psutil.disk_io_counters()
                except (OSError, RuntimeError):
                    disk_io = None
                try:
                    load_average = list(os.getloadavg())
                except (AttributeError, OSError):
                    load_average = None
                self.current = dict(timestamp=now(), cpu_percent=psutil.cpu_percent(interval=None),
                                    memory={key: getattr(memory, key) for key in ("total", "available", "used", "percent")},
                                    load_average=load_average, disks=disks, network=network,
                                    disk_io=disk_io._asdict() if disk_io else None)
                self.history.append(dict(timestamp=self.current["timestamp"], cpu_percent=self.current["cpu_percent"],
                                         memory_percent=memory.percent,
                                         network_bytes_sent=sum(n["bytes_sent"] for n in network),
                                         network_bytes_recv=sum(n["bytes_recv"] for n in network)))
                self.history = self.history[-MAX_HISTORY:]
                self.last_sample = time.monotonic()
                atomic_json(self.history_path, self.history)
            return dict(host=dict(hostname=socket.gethostname(), role=self.settings.role,
                                  platform=platform.system(), boot_time=psutil.boot_time()),
                        current=dict(self.current), history=list(self.history))

    def load_plan(self, plan_id: str) -> BackupPlan:
        if not PLAN_ID.fullmatch(plan_id):
            raise ValueError("Invalid backup plan ID.")
        directory = self.settings.config_dir / "backups"
        path = directory / f"{plan_id}.json"
        if os.name == "posix":
            for folder in (self.settings.config_dir, directory):
                if folder.is_symlink() or not folder.is_dir():
                    raise ValueError("Backup configuration directory is missing or is a symlink.")
                info = folder.stat()
                if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                    raise ValueError("Backup configuration directories must be owned by root and not group/world writable.")
        protected_file(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Backup plan must be a JSON object.")
        password_file = Path(data["password_file"])
        if not password_file.is_absolute():
            raise ValueError("password_file must be absolute.")
        protected_file(password_file, secret=True)
        repository = data["repository"]
        if not isinstance(repository, str) or not repository or "\x00" in repository:
            raise ValueError("Repository must be a nonempty path or SFTP destination.")
        sources = data.get("sources", [])
        if not isinstance(sources, list) or not sources:
            raise ValueError("At least one source directory is required.")
        for source in sources:
            if not isinstance(source, str) or not Path(source).is_absolute() or not Path(source).is_dir():
                raise ValueError(f"Source directory is missing or not absolute: {source}")
        excludes = data.get("excludes", [])
        if not isinstance(excludes, list) or any(not isinstance(x, str) or "\x00" in x for x in excludes):
            raise ValueError("excludes must be a list of patterns.")
        timeout = int(data.get("timeout_seconds", 21600))
        if not 1 <= timeout <= 86400:
            raise ValueError("timeout_seconds must be between 1 and 86400.")
        kwargs = {}
        if repository.startswith("sftp:"):
            # Require an explicit user and absolute remote directory. No URL credentials or shell commands.
            if not SFTP_REPOSITORY.fullmatch(repository):
                raise ValueError("SFTP repository must look like sftp:backup@server:/absolute/directory.")
            for key in ("ssh_key", "known_hosts"):
                value = data[key]
                if not isinstance(value, str) or not SAFE_PATH.fullmatch(value):
                    raise ValueError(f"{key} must be an absolute path without spaces or quotes.")
                credential = Path(value)
                protected_file(credential, root_owned=False, secret=True)
                kwargs[key] = credential
            destination_type = "sftp"
        else:
            destination_type = "local"
            repo = Path(repository)
            mount = Path(data.get("repository_mount", ""))
            if not repo.is_absolute() or not mount.is_absolute() or not is_mount(mount):
                raise ValueError("Local backup filesystem is not mounted at repository_mount.")
            if not repo.resolve().is_relative_to(mount.resolve()) or repo.resolve() == mount.resolve():
                raise ValueError("Repository must be inside its configured backup mount.")
            if any(device(Path(source)) == device(mount) for source in sources):
                raise ValueError("Local backups must use a separate filesystem from every source.")
            # restic crosses mount points, so a source above the mount would back up the repository itself.
            if any(repo.resolve().is_relative_to(Path(source).resolve()) for source in sources):
                raise ValueError("The backup repository cannot be inside a source directory.")
            if not repo.is_dir():
                raise ValueError("Backup repository directory is missing. Run the configuration helper first.")
            kwargs["repository_mount"] = mount
        return BackupPlan(plan_id, str(data.get("name", plan_id))[:128], repository, password_file,
                          sources, excludes, timeout, destination_type, **kwargs)

    def plans(self) -> list[dict]:
        latest: dict[str, dict] = {}
        for task in self.tasks():
            if task.get("action") == "backup" and task.get("state") in TERMINAL:
                latest.setdefault(task.get("plan_id"), task)
        items = []
        for path in sorted((self.settings.config_dir / "backups").glob("*.json")):
            last = latest.get(path.stem)
            last_backup = (dict(id=last["id"], state=last["state"], finished_at=last.get("finished_at"))
                           if last else None)
            try:
                plan = self.load_plan(path.stem)
                items.append(dict(id=plan.id, name=plan.name, sources=plan.sources,
                                  destination_type=plan.destination_type, configured=True, error=None,
                                  last_backup=last_backup))
            except (OSError, ValueError, KeyError, TypeError) as error:
                items.append(dict(id=path.stem, name=path.stem, sources=[], destination_type=None,
                                  configured=False, error=str(error), last_backup=last_backup))
        return items

    def _command(self, plan: BackupPlan) -> tuple[list[str], dict[str, str]]:
        if not self.restic:
            raise ValueError("restic is not installed. Run the server application installer.")
        command = [*self.restic, "--repo", plan.repository, "--cache-dir", str(self.root / "cache")]
        if plan.destination_type == "sftp":
            ssh = ["ssh", "-i", plan.ssh_key.as_posix(), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                   "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={plan.known_hosts.as_posix()}"]
            ssh += ["-s", plan.repository[5:].split(":", 1)[0], "sftp"]
            # Restic parses this command itself; no API field is used as shell input.
            command.extend(["-o", "sftp.command=" + shlex.join(ssh)])
        environment = {key: value for key, value in os.environ.items() if not key.startswith("RESTIC_")}
        environment["RESTIC_PASSWORD_FILE"] = str(plan.password_file)
        return command, environment

    def snapshots(self, plan_id: str) -> list[dict]:
        plan = self.load_plan(plan_id)
        command, environment = self._command(plan)
        process = subprocess.Popen(command + ["snapshots", "--json"], env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            stdout, _ = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            # Stop ssh children too; subprocess.run would wait for their pipes.
            self._terminate(process)
            process.communicate()
            raise
        if process.returncode:
            raise ValueError("Cannot read snapshots. Check the repository, credentials, and backup destination.")
        snapshots = json.loads(stdout.decode("utf-8", errors="replace"))
        return [{key: item.get(key) for key in ("id", "short_id", "time", "hostname", "paths")}
                for item in (snapshots or [])][-500:]

    def task(self, task_id: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{32}", task_id):
            raise FileNotFoundError("Unknown backup task.")
        with self.lock:
            return json.loads((self.tasks_dir / f"{task_id}.json").read_text(encoding="utf-8"))

    def tasks(self) -> list[dict]:
        with self.lock:
            items = []
            for path in self.tasks_dir.glob("*.json"):
                try:
                    task = json.loads(path.read_text(encoding="utf-8"))
                    # Full logs are available from the individual task endpoint.
                    items.append({key: value for key, value in task.items() if key != "log"})
                except (OSError, ValueError, AttributeError):
                    continue
            return sorted(items, key=lambda item: str(item.get("created_at", "")), reverse=True)

    def submit(self, plan_id: str, action: Literal["backup", "check", "restore"], snapshot_id: str = "") -> dict:
        if action not in ("backup", "check", "restore"):
            raise ValueError("Unknown backup operation.")
        if action == "restore" and not re.fullmatch(r"[a-fA-F0-9]{8,64}", snapshot_id):
            raise ValueError("Choose a specific snapshot ID from the snapshot list.")
        plan = self.load_plan(plan_id)
        command, environment = self._command(plan)
        with self.lock:
            if any(task.get("state") not in TERMINAL for task in self.tasks()):
                raise RuntimeError("A backup operation is already running on this server.")
            task_id = uuid.uuid4().hex
            task = dict(id=task_id, plan_id=plan.id, action=action, state="queued", created_at=now(),
                        started_at=None, finished_at=None, exit_code=None, log="", restore_path=None, error=None)
            if action == "backup":
                command += ["backup"]
                for pattern in plan.excludes:
                    command += ["--exclude", pattern]
                command += ["--", *plan.sources]
            elif action == "check":
                command += ["check", "--read-data"]
            else:
                restore_root = self.settings.state_dir / "restores"
                if restore_root.is_symlink():
                    raise ValueError("Restore root must not be a symlink.")
                restore_root.mkdir(mode=0o700, exist_ok=True)
                destination = restore_root / task_id
                destination.mkdir(mode=0o700)  # Never reuse an existing destination.
                task["restore_path"] = str(destination)
                command += ["restore", snapshot_id, "--target", str(destination), "--verify"]
            atomic_json(self.tasks_dir / f"{task_id}.json", task)
            self._prune_tasks()
            cancel = threading.Event()
            self.cancel_events[task_id] = cancel
            worker = threading.Thread(target=self._run, args=(task_id, command, environment, plan.timeout_seconds, cancel),
                                      name=f"backup-{task_id}", daemon=True)
            self.workers[task_id] = worker
            worker.start()
            return task

    def _update(self, task_id: str, **changes: object) -> None:
        with self.lock:
            task = self.task(task_id)
            task.update(changes)
            atomic_json(self.tasks_dir / f"{task_id}.json", task)

    @staticmethod
    def _terminate(process: subprocess.Popen) -> bool:
        """Stop the process group. Returns False when the process had already exited."""
        if process.poll() is not None:
            return False

        def send(posix_signal: int, fallback) -> None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, posix_signal)
                else:
                    fallback()
            except (ProcessLookupError, PermissionError):
                pass

        send(signal.SIGTERM, process.terminate)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            send(getattr(signal, "SIGKILL", signal.SIGTERM), process.kill)
            process.wait(timeout=5)
        if os.name == "posix":
            # Clean up ssh helpers that outlived restic in the same session.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        return True

    def _run(self, task_id: str, command: list[str], environment: dict[str, str], timeout: int,
             cancel: threading.Event) -> None:
        process = None
        reader = None
        chunks = bytearray()
        chunks_lock = threading.Lock()
        state, error = "failed", None
        try:
            self._update(task_id, state="running", started_at=now())
            process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)

            def read_output() -> None:
                while True:
                    chunk = process.stdout.read1(4096)
                    if not chunk:
                        break
                    with chunks_lock:
                        chunks.extend(chunk)
                        if len(chunks) > MAX_LOG:
                            del chunks[:-MAX_LOG]

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            started = last_flush = time.monotonic()
            written = b""
            while process.poll() is None:
                if cancel.wait(0.2):
                    if self._terminate(process):
                        with self.lock:
                            state, error = self.cancel_reasons.get(task_id, ("cancelled", "Cancelled by the user."))
                        break
                    continue  # It finished before the signal; report its real result.
                if time.monotonic() - started > timeout:
                    if self._terminate(process):
                        state, error = "failed", f"Operation exceeded its {timeout}-second time limit."
                        break
                    continue
                if time.monotonic() - last_flush >= LOG_FLUSH_SECONDS:
                    with chunks_lock:
                        snapshot = bytes(chunks)
                    if snapshot != written:
                        self._update(task_id, log=snapshot.decode("utf-8", errors="replace"))
                        written = snapshot
                    last_flush = time.monotonic()
            else:
                state = "succeeded" if process.returncode == 0 else "failed"
                if state == "failed":
                    error = f"restic exited with status {process.returncode}. See the task log."
        except Exception as exc:
            state, error = "failed", f"Operation could not finish: {type(exc).__name__}: {exc}"
            if process:
                try:
                    self._terminate(process)
                except Exception:
                    pass
        finally:
            if reader:
                reader.join(timeout=5)
            # Closing a pipe that another thread is still reading can block; leave it to the daemon reader.
            if process and process.stdout and not (reader and reader.is_alive()):
                process.stdout.close()
            with chunks_lock:
                log = chunks.decode("utf-8", errors="replace")
            try:
                self._update(task_id, state=state, error=error, finished_at=now(),
                             exit_code=process.returncode if process else None, log=log)
            finally:
                with self.lock:
                    self.cancel_events.pop(task_id, None)
                    self.cancel_reasons.pop(task_id, None)
                    self.workers.pop(task_id, None)

    def cancel(self, task_id: str) -> dict:
        with self.lock:
            task = self.task(task_id)
            event = self.cancel_events.get(task_id)
            if event and not event.is_set():
                self.cancel_reasons.setdefault(task_id, ("cancelled", "Cancelled by the user."))
                event.set()
            return task

    def close(self) -> None:
        with self.lock:
            for task_id, event in self.cancel_events.items():
                self.cancel_reasons.setdefault(task_id, ("interrupted", SHUTDOWN))
                event.set()
            workers = list(self.workers.values())
        for worker in workers:
            worker.join(timeout=12)


class PlanRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=64)


class RestoreRequest(PlanRequest):
    snapshot_id: str = Field(min_length=8, max_length=64)


def create_router(settings: Settings, restic: str | Sequence[str] | None = None) -> APIRouter:
    operations = Operations(settings, restic)

    @asynccontextmanager
    async def lifespan(_app):
        stop = asyncio.Event()

        async def collect():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(operations.metrics)
                except Exception:
                    pass  # A failed sample must not stop later samples.
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5)
                except TimeoutError:
                    pass

        collector = asyncio.create_task(collect())
        try:
            yield
        finally:
            stop.set()
            await collector
            await asyncio.to_thread(operations.close)

    router = APIRouter(lifespan=lifespan)

    def translate(function, *args):
        try:
            return function(*args)
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        except subprocess.TimeoutExpired as error:
            raise HTTPException(504, "Backup repository did not answer within 30 seconds.") from error
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise HTTPException(400, str(error)) from error

    @router.get("/metrics")
    def metrics():
        return operations.metrics()

    @router.get("/backups")
    def backups():
        return dict(available=bool(operations.restic), plans=operations.plans(), tasks=operations.tasks())

    @router.get("/backups/{plan_id}/snapshots")
    def snapshots(plan_id: str):
        return dict(items=translate(operations.snapshots, plan_id))

    @router.post("/backups/run", status_code=202)
    def run(request: PlanRequest):
        return translate(operations.submit, request.plan_id, "backup")

    @router.post("/backups/check", status_code=202)
    def check(request: PlanRequest):
        return translate(operations.submit, request.plan_id, "check")

    @router.post("/backups/restore", status_code=202)
    def restore(request: RestoreRequest):
        return translate(operations.submit, request.plan_id, "restore", request.snapshot_id)

    @router.get("/backups/tasks/{task_id}")
    def task(task_id: str):
        return translate(operations.task, task_id)

    @router.post("/backups/tasks/{task_id}/cancel")
    def cancel(task_id: str):
        return translate(operations.cancel, task_id)

    return router
