"""NAS model catalog and persistent, serial Hugging Face download queue."""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Query
import httpx
from huggingface_hub import get_hf_file_metadata, get_token, hf_hub_url
from huggingface_hub.utils import validate_repo_id
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings
from .model_format import inspect_gguf

ACTIVE = {'queued', 'resolving', 'downloading', 'validating'}
STAGING = '.homelab-downloads'
HEADROOM = 256 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 1024 ** 4  # Explicit ceiling: 1 TiB per requested file.


def now():
    return datetime.now(timezone.utc).isoformat()


def relative_name(value: str, *, top_level=False):
    if not value or len(value) > 1024 or '\\' in value or ':' in value or '\x00' in value:
        raise ValueError('Use a relative filename with forward slashes')
    parts = value.split('/')
    if any(not part or part in {'.', '..'} or part.startswith('.') or
           any(ord(c) < 32 for c in part) for part in parts):
        raise ValueError('Hidden names, empty components, and parent paths are not allowed')
    if top_level and len(parts) != 1:
        raise ValueError('local_name must be one filename, without a directory')
    if not value.lower().endswith('.gguf'):
        raise ValueError('Choose a .gguf file')
    return value


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    repo_id: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=1024)
    revision: str = Field(default='main', min_length=1, max_length=200)
    local_name: str | None = Field(default=None, max_length=240)

    @field_validator('repo_id')
    @classmethod
    def repo_valid(cls, value):
        validate_repo_id(value)
        return value

    @field_validator('filename')
    @classmethod
    def filename_valid(cls, value):
        return relative_name(value)

    @field_validator('local_name')
    @classmethod
    def local_valid(cls, value):
        return relative_name(value, top_level=True) if value is not None else None

    @field_validator('revision')
    @classmethod
    def revision_valid(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError('Invalid revision')
        return value


@contextmanager
def safe_open(root: Path, relative: str):
    """Open an existing regular file, refusing symlinks at every path component.

    Linux uses directory descriptors and O_NOFOLLOW so a rename race cannot
    redirect a read outside the root. The Windows fallback is for local tests.
    """
    relative_name(relative)
    parts = relative.split('/')
    if os.name == 'posix':
        directories = []
        try:
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            directories.append(descriptor)
            for part in parts[:-1]:
                descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                directories.append(descriptor)
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('Model is not a regular file')
                yield stream
        finally:
            for descriptor in reversed(directories):
                os.close(descriptor)
    else:
        candidate = root
        for part in parts:
            candidate /= part
            if candidate.is_symlink() or getattr(candidate, 'is_junction', lambda: False)():
                raise ValueError('Linked files and folders are not supported')
        if not candidate.resolve().is_relative_to(root.resolve()) or not candidate.is_file():
            raise ValueError('Model is outside its root or is not a file')
        with candidate.open('rb') as stream:
            yield stream


class DownloadCancelled(Exception):
    pass


class DownloadStopped(Exception):
    pass


class DownloadStage:
    """Same-filesystem staging with descriptor-relative operations on Linux."""
    def __init__(self, root, task_id):
        self.root = root
        self.filename = task_id + '.partial'
        self.folder = root / STAGING
        self.root_fd = self.stage_fd = None

    def __enter__(self):
        if os.name == 'posix':
            self.root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                try:
                    os.mkdir(STAGING, mode=0o700, dir_fd=self.root_fd)
                except FileExistsError:
                    pass
                self.stage_fd = os.open(STAGING, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.root_fd)
                # Group follows the NAS root, so the final 0640 model is readable
                # by homelabshare. Setgid persists that group for new files.
                os.fchown(self.stage_fd, -1, os.fstat(self.root_fd).st_gid)
                os.fchmod(self.stage_fd, 0o2700)
            except Exception:
                os.close(self.root_fd)
                self.root_fd = None
                if self.stage_fd is not None:
                    os.close(self.stage_fd)
                    self.stage_fd = None
                raise
        else:
            self.folder.mkdir(mode=0o700, exist_ok=True)
            if self.folder.is_symlink() or self.folder.resolve() != self.folder:
                raise ValueError('Download staging directory must not be a symlink')
        return self

    def unlink(self):
        if self.stage_fd is not None:
            try:
                os.unlink(self.filename, dir_fd=self.stage_fd)
            except FileNotFoundError:
                pass
        else:
            (self.folder / self.filename).unlink(missing_ok=True)

    def open(self, writing=False):
        if self.stage_fd is not None:
            flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL) if writing else os.O_RDONLY
            fd = os.open(self.filename, flags | os.O_NOFOLLOW, mode=0o640, dir_fd=self.stage_fd)
            return os.fdopen(fd, 'wb' if writing else 'rb')
        return (self.folder / self.filename).open('xb' if writing else 'rb')

    def publish(self, name):
        if self.stage_fd is not None:
            os.link(self.filename, name, src_dir_fd=self.stage_fd, dst_dir_fd=self.root_fd,
                    follow_symlinks=False)
            os.fsync(self.root_fd)
        else:
            os.link(self.folder / self.filename, self.root / name, follow_symlinks=False)

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.unlink()
        finally:
            if self.stage_fd is not None:
                os.close(self.stage_fd)
            if self.root_fd is not None:
                os.close(self.root_fd)


class HubTransport:
    """Only model-repository URLs generated by the official HF library are used."""
    def metadata(self, task):
        # A recovered task remains pinned even if the original branch changed.
        revision = task.get('resolved_revision') or task['revision']
        url = hf_hub_url(task['repo_id'], task['filename'], revision=revision,
                         endpoint='https://huggingface.co')
        metadata = get_hf_file_metadata(url, token=get_token(), timeout=20,
                                        retry_on_errors=False)
        commit = metadata.commit_hash
        if not commit or not re.fullmatch(r'[0-9a-f]{40,64}', commit):
            raise ValueError('Hub did not provide a resolved commit')
        if not isinstance(metadata.size, int) or not 24 <= metadata.size <= MAX_DOWNLOAD_BYTES:
            raise ValueError('Hub did not provide a supported file size (maximum 1 TiB)')
        etag = (metadata.etag or '').strip('"')
        return {'commit': commit, 'size': metadata.size,
                'sha256': etag.lower() if re.fullmatch(r'[0-9a-fA-F]{64}', etag) else None}

    def chunks(self, task):
        url = hf_hub_url(task['repo_id'], task['filename'], revision=task['resolved_revision'],
                         endpoint='https://huggingface.co')
        token = get_token()
        headers = {'Accept-Encoding': 'identity'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        # HTTPX removes Authorization on redirects to a different host. Redirect
        # hops are limited and TLS verification remains on. No URL comes from API input.
        def require_https(request):
            if request.url.scheme != 'https':
                raise ValueError('Hub redirected outside HTTPS')

        with httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=True,
                          max_redirects=8, trust_env=False,
                          event_hooks={'request': [require_https]}) as client:
            with client.stream('GET', url, headers=headers) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    yield chunk


class ModelLibrary:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.root = settings.models_dir.absolute()
        self.state = settings.state_dir / 'models'
        self.state.mkdir(parents=True, exist_ok=True)
        if self.state.is_symlink():
            raise ValueError('Model state directory may not be a symlink')
        self.database = self.state / 'downloads.json'
        self.lock = threading.RLock()
        self.hash_lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.transport = transport or HubTransport()
        self.tasks = {}
        self.cache = {}
        if self.database.exists():
            document = json.loads(self.database.read_text(encoding='utf-8'))
            for task in document.get('items', []):
                if not re.fullmatch(r'[0-9a-f]{32}', task['id']):
                    raise ValueError('Invalid saved download ID')
                DownloadRequest(**{key: task[key] for key in ('repo_id', 'filename', 'revision', 'local_name')})
                if task['state'] in ACTIVE:
                    task['state'] = 'cancelled' if task.get('cancel_requested') else 'queued'
                    task['downloaded_bytes'] = 0
                    task['progress'] = 0
                    task['error'] = 'Interrupted by restart; restarting from byte zero at the saved revision.'
                self.tasks[task['id']] = task
        self._save()

    def _save(self):
        with self.lock:
            temporary = self.state / ('downloads.' + uuid.uuid4().hex + '.tmp')
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump({'version': 1, 'items': list(self.tasks.values())}, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.database)

    def _update(self, task_id, **values):
        with self.lock:
            self.tasks[task_id].update(values, updated_at=now())
            self._save()

    def _root_ready(self):
        if not self.root.is_dir() or self.root.is_symlink() or self.root.resolve() != self.root:
            raise ValueError('Models directory is missing or uses a symlink; mount the NAS storage first')

    def start(self):
        if not self.thread or not self.thread.is_alive():
            self.stopping.clear()
            self.thread = threading.Thread(target=self._worker, name='model-downloads', daemon=True)
            self.thread.start()

    def stop(self):
        self.stopping.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _worker(self):
        while not self.stopping.is_set():
            if not self.run_next():
                self.wake.wait(1)
                self.wake.clear()

    def list_tasks(self):
        with self.lock:
            return {'items': [dict(task) for task in reversed(list(self.tasks.values()))]}

    def get(self, task_id):
        with self.lock:
            if task_id not in self.tasks:
                raise HTTPException(404, 'Download not found')
            return dict(self.tasks[task_id])

    def enqueue(self, request: DownloadRequest):
        if not self.settings.allow_network_downloads:
            raise HTTPException(403, 'Network downloads are disabled in server configuration')
        try:
            self._root_ready()
        except ValueError as error:
            raise HTTPException(503, str(error)) from error
        local_name = request.local_name or PurePosixPath(request.filename).name
        relative_name(local_name, top_level=True)
        with self.lock:
            if os.path.lexists(self.root / local_name):
                raise HTTPException(409, 'That local filename already exists; choose another name')
            if any(t['local_name'].casefold() == local_name.casefold() and t['state'] in ACTIVE for t in self.tasks.values()):
                raise HTTPException(409, 'That local filename already has a pending download')
            # SMB clients are generally case-insensitive even if the NAS filesystem is not.
            if any(p.name.casefold() == local_name.casefold() for p in self.root.iterdir()):
                raise HTTPException(409, 'A filename with that spelling already exists')
            if len(self.tasks) >= 1000:
                raise HTTPException(409, 'Download history has reached 1000 entries; archive downloads.json while the service is stopped')
            stamp, task_id = now(), uuid.uuid4().hex
            task = {'id': task_id, 'state': 'queued', 'repo_id': request.repo_id,
                    'filename': request.filename, 'revision': request.revision,
                    'resolved_revision': None, 'local_name': local_name,
                    'downloaded_bytes': 0, 'total_bytes': None, 'progress': 0,
                    'error': None, 'created_at': stamp, 'updated_at': stamp,
                    'model_id': None, 'cancel_requested': False, 'sha256': None}
            self.tasks[task_id] = task
            self._save()
            self.wake.set()
            return dict(task)

    def cancel(self, task_id):
        with self.lock:
            task = self.get(task_id)
            if task['state'] in ACTIVE:
                updates = {'cancel_requested': True}
                if task['state'] == 'queued':
                    updates['state'] = 'cancelled'
                self._update(task_id, **updates)
            return self.get(task_id)

    def _check_cancel(self, task_id):
        if self.get(task_id).get('cancel_requested'):
            raise DownloadCancelled()
        if self.stopping.is_set():
            raise DownloadStopped()

    def run_next(self):
        """Execute one queued download. The service calls this on a single worker."""
        with self.lock:
            pending = next((t for t in self.tasks.values() if t['state'] == 'queued'), None)
            if not pending:
                return False
            task_id = pending['id']
            self._update(task_id, state='resolving', error=None)
        stage = None
        try:
            if not self.settings.allow_network_downloads:
                raise ValueError('Network downloads are disabled in server configuration')
            self._root_ready()
            self._check_cancel(task_id)
            task = self.get(task_id)
            # Recover a completed atomic publish if power failed before its state write.
            if task.get('sha256') and (self.root / task['local_name']).exists():
                try:
                    existing = self.hash(task['local_name'])
                except HTTPException:
                    existing = None  # Someone else's file; the publish step refuses to replace it.
                if existing and existing['sha256'] == task['sha256'] and existing['size_bytes'] == task['total_bytes']:
                    self._update(task_id, state='completed', model_id=task['local_name'], progress=100,
                                 downloaded_bytes=task['total_bytes'])
                    return True
            metadata = self.transport.metadata(task)
            self._check_cancel(task_id)
            if not 24 <= metadata['size'] <= MAX_DOWNLOAD_BYTES or not re.fullmatch(r'[0-9a-f]{40,64}', metadata['commit']):
                raise ValueError('Unsupported remote metadata')
            if task.get('resolved_revision') and task['resolved_revision'] != metadata['commit']:
                raise ValueError('Hub returned a different revision from the pinned task')
            self._update(task_id, resolved_revision=metadata['commit'], total_bytes=metadata['size'])
            stage = DownloadStage(self.root, task_id)
            stage.__enter__()
            # Recovered unfinished downloads restart deterministically at the pinned commit.
            stage.unlink()
            if shutil.disk_usage(self.root).free < metadata['size'] + HEADROOM:
                raise ValueError('Not enough free disk space for this model plus 256 MiB headroom')
            self._update(task_id, state='downloading', downloaded_bytes=0, progress=0)
            task = self.get(task_id)
            digest, count, last_save = hashlib.sha256(), 0, 0.0
            deadline = time.monotonic() + 24 * 3600
            with stage.open(writing=True) as stream:
                for chunk in self.transport.chunks(task):
                    self._check_cancel(task_id)
                    if time.monotonic() > deadline:
                        raise ValueError('Download exceeded the 24-hour limit')
                    count += len(chunk)
                    if count > metadata['size']:
                        raise ValueError('Download exceeded its advertised size')
                    stream.write(chunk)
                    digest.update(chunk)
                    if time.monotonic() - last_save >= 0.5:
                        self._update(task_id, downloaded_bytes=count, progress=round(count / metadata['size'] * 100, 1))
                        last_save = time.monotonic()
                stream.flush()
                os.fsync(stream.fileno())
            self._check_cancel(task_id)
            if count != metadata['size']:
                raise ValueError('Download was incomplete; its byte count did not match the Hub')
            checksum = digest.hexdigest()
            if metadata.get('sha256') and checksum != metadata['sha256']:
                raise ValueError('Download checksum did not match the Hub')
            self._update(task_id, state='validating', downloaded_bytes=count, sha256=checksum)
            with stage.open() as stream:
                inspect_gguf(stream)
            self._check_cancel(task_id)
            # Hard-link publish is atomic and cannot replace an existing filename.
            # Both paths are on the same NAS filesystem; stage is service-private.
            self._root_ready()
            if any(p.name.casefold() == task['local_name'].casefold() for p in self.root.iterdir()):
                raise ValueError('The destination now exists; it was left unchanged')
            stage.publish(task['local_name'])
            self._update(task_id, state='completed', model_id=task['local_name'], progress=100,
                         downloaded_bytes=count, error=None)
        except DownloadCancelled:
            self._update(task_id, state='cancelled', error='Cancelled by user')
        except DownloadStopped:
            self._update(task_id, state='queued', error='Service stopped; download will restart at the saved revision')
        except Exception as error:
            # Network exception text may contain signed URLs or credentials; never expose it.
            if isinstance(error, (ValueError, FileExistsError)):
                message = str(error)[:300]
            elif isinstance(error, httpx.HTTPStatusError):
                message = f'Hub download returned HTTP {error.response.status_code}; check access, filename, and revision'
            elif isinstance(error, OSError):
                message = 'Storage operation failed; check mounts, space, and service permissions'
            else:
                message = 'Hub request failed; check network access, repository permission, filename, and revision'
            self._update(task_id, state='failed', error=message)
        finally:
            if stage is not None and (stage.stage_fd is not None or (os.name != 'posix' and stage.folder.is_dir() and not stage.folder.is_symlink())):
                stage.__exit__(None, None, None)
        return True

    def catalog(self):
        try:
            self._root_ready()
        except ValueError as error:
            raise HTTPException(503, str(error)) from error
        items, warnings = [], []
        with self.lock:
            sources = {task['local_name']: {'repo_id': task['repo_id'], 'filename': task['filename'],
                                          'revision': task['resolved_revision']}
                       for task in self.tasks.values() if task['state'] == 'completed'}
        seen = set()
        for directory, folders, files in os.walk(self.root, followlinks=False):
            folders[:] = [name for name in folders if not name.startswith('.') and
                          not (Path(directory) / name).is_symlink() and
                          not getattr(Path(directory) / name, 'is_junction', lambda: False)()]
            for filename in files:
                if filename.startswith('.') or not filename.lower().endswith('.gguf'):
                    continue
                model_id = (Path(directory) / filename).relative_to(self.root).as_posix()
                try:
                    with safe_open(self.root, model_id) as stream:
                        before = os.fstat(stream.fileno())
                        signature = (before.st_size, before.st_mtime_ns, before.st_ino)
                        cached = self.cache.get(model_id)
                        details = cached[1] if cached and cached[0] == signature else inspect_gguf(stream)
                        after = os.fstat(stream.fileno())
                        if signature != (after.st_size, after.st_mtime_ns, after.st_ino):
                            raise ValueError('File is being modified; scan again after copying finishes')
                        self.cache[model_id] = (signature, details)
                        seen.add(model_id)
                        items.append({'id': model_id, 'name': filename, 'size_bytes': before.st_size,
                                      'modified_at': datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat(),
                                      'format': 'GGUF', **details, 'source': sources.get(model_id)})
                except (OSError, ValueError) as error:
                    if len(warnings) < 100:
                        warnings.append({'id': model_id, 'reason': str(error)[:200] if isinstance(error, ValueError) else 'Unreadable file or symlink'})
        self.cache = {key: value for key, value in self.cache.items() if key in seen}
        return {'items': sorted(items, key=lambda item: item['id'].casefold()),
                'total_bytes': sum(item['size_bytes'] for item in items), 'warnings': warnings}

    def hash(self, model_id):
        try:
            self._root_ready()
            with self.hash_lock, safe_open(self.root, model_id) as stream:
                before = os.fstat(stream.fileno())
                inspect_gguf(stream)
                stream.seek(0)
                checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError('File changed while hashing; retry after copying finishes')
                return {'id': model_id, 'sha256': checksum, 'size_bytes': before.st_size}
        except (OSError, ValueError) as error:
            raise HTTPException(400, 'Model must be a complete GGUF inside the NAS root') from error


def create_router(settings: Settings) -> APIRouter:
    if settings.role != 'hp':
        return APIRouter()
    library = ModelLibrary(settings)

    @asynccontextmanager
    async def lifespan(app):
        library.start()
        try:
            yield
        finally:
            library.stop()

    router = APIRouter(lifespan=lifespan)

    @router.get('/models')
    def list_models():
        return library.catalog()

    @router.post('/models/scan')
    def scan_models():
        return library.catalog()

    @router.get('/models/hash')
    def hash_model(model_id: str = Query(min_length=1, max_length=1024)):
        return library.hash(model_id)

    @router.get('/downloads')
    def downloads():
        return library.list_tasks()

    @router.post('/downloads', status_code=202)
    def download(request: DownloadRequest):
        return library.enqueue(request)

    @router.get('/downloads/{task_id}')
    def download_status(task_id: str):
        return library.get(task_id)

    @router.post('/downloads/{task_id}/cancel')
    def cancel_download(task_id: str):
        return library.cancel(task_id)

    return router
