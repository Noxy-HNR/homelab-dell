"""Small, durable developer workbench. Jobs run as the unprivileged service user."""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from typing import Literal
import uuid

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
GITHUB = re.compile(r"https://github\.com/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}(?:\.git)?\Z")
MAX_FILE_BYTES = 256 * 1024
MAX_INDEX_FILES = 2000
MAX_TREE_ENTRIES = 50000
MAX_LOG_BYTES = 128 * 1024
OMIT_DIRS = {".git", ".venv", "venv", "node_modules", "vendor", "dist", "build", "__pycache__", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".rst", ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg", ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".sql", ".sh", ".ps1", ".xml", ".csv"}
TEXT_NAMES = {"dockerfile", "makefile", "license", ".gitignore", ".dockerignore"}
DEFAULT_PRESETS = {"test": {"args": ["-m", "unittest", "discover", "-s", "tests"], "timeout_seconds": 900}, "compile": {"args": ["-m", "compileall", "-q", "."], "timeout_seconds": 900}}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _private_name(name: str) -> bool:
    name = name.lower()
    return name == ".env" or name.startswith((".env.", "id_rsa", "id_ed25519", "credentials.", "secrets.")) or name.endswith((".pem", ".key", ".p12", ".pfx"))


def _parts(relative: str) -> tuple[str, ...]:
    if not relative or "\\" in relative or "\x00" in relative or ":" in relative:
        raise HTTPException(400, "Use a relative project file path with forward slashes.")
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in (".", "..", "") for part in relative.split("/")):
        raise HTTPException(400, "Path must stay inside its project.")
    parts = path.parts
    if any(part in OMIT_DIRS or _private_name(part) for part in parts):
        raise HTTPException(403, "Generated, dependency, Git, and credential paths are excluded.")
    return parts


def _safe_root(root: Path) -> Path:
    if not root.is_dir() or _link(root):
        raise HTTPException(409, "Project directory is missing or is a symbolic link.")
    return root


def _read_file(root: Path, relative: str) -> str:
    """Use no-follow directory descriptors on Linux, including every ancestor."""
    parts = _parts(relative)
    _safe_root(root)
    file_path = root.joinpath(*parts)
    current = root
    for part in parts:
        current = current / part
        if not current.exists() or _link(current):
            raise HTTPException(404, "File is missing or is a symbolic link.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    directories: list[int] = []
    descriptor = None
    try:
        if os.name == "posix":
            parent = os.open(root, flags | os.O_DIRECTORY)
            directories.append(parent)
            for part in parts[:-1]:
                parent = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent)
                directories.append(parent)
            descriptor = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=parent)
        else:
            descriptor = os.open(file_path, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise HTTPException(400, "Only regular text files can be read.")
        if info.st_size > MAX_FILE_BYTES:
            raise HTTPException(413, "File is larger than the 256 KiB read limit.")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, "File is larger than the 256 KiB read limit.")
        if b"\x00" in data:
            raise HTTPException(415, "Binary files cannot be displayed.")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(415, "Only UTF-8 text files can be displayed.") from exc
    except OSError as exc:
        raise HTTPException(400, "File could not be read safely.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for directory in reversed(directories):
            os.close(directory)


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=48)
    template: Literal["python"] | None = None
    git_url: str | None = Field(default=None, max_length=220)


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=48)
    preset: str = Field(min_length=1, max_length=48)


class ReindexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str | None = Field(default=None, max_length=48)


class Workbench:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.state = settings.state_dir / "workbench"
        self.state.mkdir(parents=True, exist_ok=True)
        settings.projects_dir.mkdir(parents=True, exist_ok=True)
        if _link(settings.projects_dir):
            raise ValueError("projects_dir must be a real directory")
        self.db_path = self.state / "workbench.sqlite3"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._active_id: str | None = None
        self._process_lock = threading.RLock()
        self._worker_lock = None
        self._creation_lock = threading.Lock()
        self.presets = self._load_presets()
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, preset TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, exit_code INTEGER, log TEXT NOT NULL DEFAULT '', log_truncated INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0, args_json TEXT NOT NULL, timeout_seconds INTEGER NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS files USING fts5(project_id UNINDEXED, path UNINDEXED, content, tokenize='unicode61');
            """)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=20)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _load_presets(self) -> dict:
        presets = dict(DEFAULT_PRESETS)
        path = self.settings.config_dir / "workbench.json"
        if path.exists():
            if _link(path) or (os.name == "posix" and (path.stat().st_uid != 0 or path.stat().st_mode & 0o022)):
                raise ValueError("workbench.json must be root owned and not group/world writable")
            config = json.loads(path.read_text(encoding="utf-8"))
            if set(config) - {"presets"}:
                raise ValueError("workbench.json only accepts presets")
            presets.update(config.get("presets", {}))
        for name, preset in presets.items():
            if not NAME.fullmatch(name) or not isinstance(preset, dict) or set(preset) != {"args", "timeout_seconds"}:
                raise ValueError("Invalid job preset")
            args = preset["args"]
            if not isinstance(args, list) or not args or len(args) > 32 or not all(isinstance(arg, str) and len(arg) < 2048 and "\x00" not in arg for arg in args):
                raise ValueError("Preset args must be a bounded list of Python arguments")
            timeout = preset["timeout_seconds"]
            if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 3600:
                raise ValueError("Preset timeout must be 1 to 3600 seconds")
        return presets

    def _root(self, project_id: str) -> Path:
        if not NAME.fullmatch(project_id):
            raise HTTPException(400, "Invalid project name.")
        root = self.settings.projects_dir / project_id
        return _safe_root(root)

    def get_project(self, project_id: str) -> dict:
        with self.db() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Project not found.")
        result = dict(row)
        result["presets"] = sorted(self.presets)
        return result

    def list_projects(self) -> dict:
        with self.db() as db:
            items = [dict(row) for row in db.execute("SELECT * FROM projects ORDER BY name")]
        for item in items:
            item["presets"] = sorted(self.presets)
        return {"items": items}

    def create_project(self, request: ProjectCreate) -> dict:
        if not NAME.fullmatch(request.name):
            raise HTTPException(400, "Name must use lowercase letters, numbers, and hyphens (48 characters maximum).")
        if bool(request.template) == bool(request.git_url):
            raise HTTPException(400, "Choose the Python template or a public GitHub HTTPS URL.")
        if request.git_url and not GITHUB.fullmatch(request.git_url):
            raise HTTPException(400, "Use https://github.com/OWNER/REPOSITORY with no credentials or extra URL parts.")
        if request.git_url and not self.settings.allow_network_downloads:
            raise HTTPException(403, "Network downloads are disabled.")
        target = self.settings.projects_dir / request.name
        pending = self.settings.projects_dir / f".pending-{request.name}-{uuid.uuid4().hex}"
        with self._creation_lock:
            with self.db() as db:
                if db.execute("SELECT 1 FROM projects WHERE id=?", (request.name,)).fetchone() or target.exists() or target.is_symlink():
                    raise HTTPException(409, "That project name is already in use.")
            try:
                if request.template:
                    self._template(pending, request.name)
                else:
                    self._clone(request.git_url or "", pending)
                self._check_tree(pending)
                pending.rename(target)
                try:
                    with self.db() as db:
                        db.execute("INSERT INTO projects VALUES (?,?,?,?)", (request.name, request.name, request.git_url or "template:python", utcnow()))
                except Exception:
                    target.rename(pending)
                    raise
            finally:
                if pending.exists() and not _link(pending):
                    shutil.rmtree(pending)
        return self.get_project(request.name)

    def _template(self, path: Path, name: str):
        path.mkdir()
        (path / "tests").mkdir()
        (path / "app.py").write_text('"""Your new Python project."""\n\ndef greet(name: str) -> str:\n    return f"Hello, {name}!"\n\nif __name__ == "__main__":\n    print(greet("homelab"))\n', encoding="utf-8")
        (path / "tests" / "test_app.py").write_text('import unittest\nfrom app import greet\n\nclass GreetingTests(unittest.TestCase):\n    def test_greeting(self):\n        self.assertEqual(greet("homelab"), "Hello, homelab!")\n', encoding="utf-8")
        (path / "README.md").write_text(f"# {name}\n\nEdit app.py, then run the test preset in the workbench.\n\nLocal commands:\n\n```sh\npython -m unittest discover -s tests\npython -m compileall -q .\n```\n", encoding="utf-8")
        (path / ".gitignore").write_text("__pycache__/\n.venv/\n.env\n", encoding="utf-8")
        (path / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "0.1.0"\nrequires-python = ">=3.12"\ndependencies = []\n', encoding="utf-8")

    @staticmethod
    def _environment() -> dict[str, str]:
        keys = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")
        env = {key: os.environ[key] for key in keys if key in os.environ}
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
        return env

    def _clone(self, url: str, destination: Path):
        try:
            result = subprocess.run(["git", "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never", "-c", f"core.hooksPath={os.devnull}", "clone", "--depth", "1", "--no-recurse-submodules", "--", url, str(destination)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=self._environment(), timeout=90, check=False)
        except FileNotFoundError as exc:
            raise HTTPException(503, "Git is not installed on the Dell.") from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "GitHub import timed out after 90 seconds.") from exc
        if result.returncode:
            raise HTTPException(400, "GitHub import failed. Check that the repository is public and reachable.")

    def _check_tree(self, root: Path):
        count = 0
        for parent, directories, files in os.walk(root, followlinks=False):
            for name in directories + files:
                path = Path(parent) / name
                count += 1
                if count > MAX_TREE_ENTRIES:
                    raise HTTPException(413, "Project exceeds the 50,000 entry limit.")
                if _link(path):
                    raise HTTPException(400, "Projects with symbolic links or directory junctions are not supported.")
            directories[:] = [name for name in directories if name not in OMIT_DIRS]

    def create_job(self, request: JobCreate) -> dict:
        self.get_project(request.project_id)
        root = self._root(request.project_id)
        self._check_tree(root)
        preset = self.presets.get(request.preset)
        if preset is None:
            raise HTTPException(400, "Unknown job preset. Select a preset listed for this project.")
        job_id = uuid.uuid4().hex
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= 20:
                raise HTTPException(429, "The job queue is full (20 active jobs).")
            db.execute("INSERT INTO jobs (id,project_id,preset,state,created_at,args_json,timeout_seconds) VALUES (?,?,?,'queued',?,?,?)", (job_id, request.project_id, request.preset, utcnow(), json.dumps(preset["args"]), preset["timeout_seconds"]))
        self._wake.set()
        return self.get_job(job_id)

    @staticmethod
    def _public_job(row) -> dict:
        result = dict(row)
        result.pop("args_json", None)
        result.pop("cancel_requested", None)
        result["log_truncated"] = bool(result["log_truncated"])
        return result

    def get_job(self, job_id: str) -> dict:
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Job not found.")
        return self._public_job(row)

    def list_jobs(self) -> dict:
        with self.db() as db:
            return {"items": [self._public_job(row) for row in db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100")]}

    def cancel_job(self, job_id: str) -> dict:
        self.get_job(job_id)
        with self.db() as db:
            db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=? AND state='running'", (job_id,))
            db.execute("UPDATE jobs SET state='cancelled',cancel_requested=1,finished_at=? WHERE id=? AND state='queued'", (utcnow(), job_id))
        self._wake.set()
        return self.get_job(job_id)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._worker_lock = open(self.state / "worker.lock", "a+b")
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(self._worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                self._worker_lock.seek(0)
                self._worker_lock.write(b"1")
                self._worker_lock.flush()
                self._worker_lock.seek(0)
                msvcrt.locking(self._worker_lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self._worker_lock.close()
            self._worker_lock = None
            raise RuntimeError("A workbench worker is already running; use one Uvicorn worker.")
        with self.db() as db:
            db.execute("UPDATE jobs SET state='interrupted', finished_at=? WHERE state='running'", (utcnow(),))
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="workbench-jobs", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                raise RuntimeError("Workbench worker did not stop cleanly")
            self._thread = None
        if self._worker_lock:
            self._worker_lock.close()
            self._worker_lock = None

    def _worker(self):
        while not self._stop.is_set():
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
                if row is not None:
                    db.execute("UPDATE jobs SET state='running', started_at=? WHERE id=?", (utcnow(), row["id"]))
            if row is None:
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            try:
                self._run(dict(row))
            except Exception as exc:
                self._append_log(row["id"], f"\nJob failed: {type(exc).__name__}: {str(exc)[:300]}\n".encode())
                self._finish(row["id"], "failed", None)

    def _append_log(self, job_id: str, data: bytes):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT log,log_truncated FROM jobs WHERE id=?", (job_id,)).fetchone()
            combined = row["log"].encode("utf-8") + data
            truncated = bool(row["log_truncated"]) or len(combined) > MAX_LOG_BYTES
            text = combined[-MAX_LOG_BYTES:].decode("utf-8", errors="replace")
            db.execute("UPDATE jobs SET log=?,log_truncated=? WHERE id=?", (text, int(truncated), job_id))

    def _finish(self, job_id: str, state: str, code: int | None):
        with self.db() as db:
            db.execute("UPDATE jobs SET state=?,exit_code=?,finished_at=? WHERE id=?", (state, code, utcnow(), job_id))

    def _kill(self, process: subprocess.Popen):
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            # A child can outlive its parent and keep stdout open.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()

    def _run(self, job: dict):
        job_id = job["id"]
        root = self._root(job["project_id"])
        self._check_tree(root)
        with self.db() as db:
            cancelled = db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        if cancelled or self._stop.is_set():
            self._finish(job_id, "cancelled" if cancelled else "interrupted", None)
            return
        process = subprocess.Popen([sys.executable, *json.loads(job["args_json"])], cwd=root, env=self._environment(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=os.name == "posix", creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        self._process = process
        self._active_id = job_id
        def read_log():
            assert process.stdout is not None
            while chunk := process.stdout.read1(4096):
                self._append_log(job_id, chunk)
        reader = threading.Thread(target=read_log, name="workbench-log", daemon=True)
        reader.start()
        deadline = time.monotonic() + job["timeout_seconds"]
        state = None
        try:
            while process.poll() is None:
                with self.db() as db:
                    cancel = db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                if cancel or self._stop.is_set() or time.monotonic() >= deadline:
                    state = "cancelled" if cancel else "interrupted" if self._stop.is_set() else "timed_out"
                    self._kill(process)
                    break
                time.sleep(0.1)
            reader.join(timeout=1)
            if reader.is_alive():
                self._kill(process)
                reader.join(timeout=4)
            if state is None:
                with self.db() as db:
                    cancel = db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                state = "cancelled" if cancel else "succeeded" if process.returncode == 0 else "failed"
            self._finish(job_id, state, process.returncode)
        finally:
            if process.poll() is None:
                self._kill(process)
            if process.stdout and not reader.is_alive():
                process.stdout.close()
            self._process = None
            self._active_id = None

    def reindex(self, project_id: str | None = None) -> dict:
        projects = [self.get_project(project_id)] if project_id else self.list_projects()["items"]
        indexed, skipped = 0, 0
        for project in projects:
            root = self._root(project["id"])
            rows = []
            count = 0
            stop_scan = False
            for parent, directories, files in os.walk(root, followlinks=False):
                safe_dirs = []
                for name in directories:
                    if name in OMIT_DIRS or _private_name(name) or _link(Path(parent) / name):
                        skipped += 1
                    else:
                        safe_dirs.append(name)
                directories[:] = safe_dirs
                for name in files:
                    count += 1
                    if count > MAX_TREE_ENTRIES or len(rows) >= MAX_INDEX_FILES:
                        skipped += 1
                        stop_scan = True
                        break
                    path = Path(parent) / name
                    if _private_name(name) or (path.suffix.lower() not in TEXT_SUFFIXES and name.lower() not in TEXT_NAMES):
                        skipped += 1
                        continue
                    relative = path.relative_to(root).as_posix()
                    try:
                        content = _read_file(root, relative)
                    except HTTPException:
                        skipped += 1
                        continue
                    rows.append((project["id"], relative, content))
                if stop_scan:
                    break
            with self.db() as db:
                db.execute("DELETE FROM files WHERE project_id=?", (project["id"],))
                db.executemany("INSERT INTO files(project_id,path,content) VALUES (?,?,?)", rows)
            indexed += len(rows)
        return {"indexed_files": indexed, "skipped_files": skipped}

    def search(self, query: str, project_id: str | None = None) -> dict:
        # User text becomes a phrase, not executable FTS query syntax.
        query = query.strip()
        if not query or len(query) > 200:
            raise HTTPException(400, "Search must be 1 to 200 characters.")
        if project_id:
            self.get_project(project_id)
        phrase = '"' + query.replace('"', '""') + '"'
        sql = "SELECT project_id,path,snippet(files,2,'[',']',' ... ',24) AS snippet FROM files WHERE files MATCH ?"
        args = [phrase]
        if project_id:
            sql += " AND project_id=?"
            args.append(project_id)
        sql += " ORDER BY rank LIMIT 30"
        with self.db() as db:
            rows = [dict(row) for row in db.execute(sql, args)]
        # Stale indexes must not reveal deleted files or replaced symlink paths.
        safe = []
        for row in rows:
            try:
                _read_file(self._root(row["project_id"]), row["path"])
            except HTTPException:
                continue
            safe.append(row)
        return {"items": safe}

    def read_file(self, project_id: str, path: str) -> dict:
        self.get_project(project_id)
        return {"project_id": project_id, "path": path, "content": _read_file(self._root(project_id), path), "truncated": False}


def create_router(settings: Settings) -> APIRouter:
    if settings.role != "dell":
        return APIRouter()
    workbench = Workbench(settings)

    @asynccontextmanager
    async def lifespan(app):
        workbench.start()
        try:
            yield
        finally:
            workbench.stop()

    router = APIRouter(lifespan=lifespan)

    @router.get("/projects")
    def projects():
        return workbench.list_projects()

    @router.post("/projects", status_code=201)
    def create_project(request: ProjectCreate):
        return workbench.create_project(request)

    @router.get("/jobs")
    def jobs():
        return workbench.list_jobs()

    @router.post("/jobs", status_code=202)
    def create_job(request: JobCreate):
        return workbench.create_job(request)

    @router.get("/jobs/{job_id}")
    def job(job_id: str):
        return workbench.get_job(job_id)

    @router.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        return workbench.cancel_job(job_id)

    @router.get("/search")
    def search(q: str = Query(min_length=1, max_length=200), project_id: str | None = None):
        return workbench.search(q, project_id)

    @router.post("/search/reindex")
    def reindex(request: ReindexRequest):
        return workbench.reindex(request.project_id)

    @router.get("/files")
    def read_file(project_id: str, path: str):
        return workbench.read_file(project_id, path)

    return router
