"""HP model catalog and download queue tests. No network or real model files."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import struct
import time
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from homelab import models
from homelab.config import Settings
from homelab.model_format import inspect_gguf
from homelab.models import DownloadRequest, ModelLibrary, safe_open

COMMIT = "a" * 40


def _string(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack("<Q", len(data)) + data


def make_gguf(name="tiny", architecture="llama", tensor_type=0, elements=32, width=4) -> bytes:
    """A minimal valid GGUF v3 file with one F32 tensor and a string array."""
    kv = [
        _string("general.architecture") + struct.pack("<I", 8) + _string(architecture),
        _string("general.name") + struct.pack("<I", 8) + _string(name),
        _string("general.file_type") + struct.pack("<I", 4) + struct.pack("<I", 0),
        _string("tokenizer.ggml.tokens") + struct.pack("<I", 9) + struct.pack("<IQ", 8, 2) + _string("a") + _string("b"),
        _string("llama.rope.scale") + struct.pack("<I", 6) + struct.pack("<f", 1.0),
    ]
    header = b"GGUF" + struct.pack("<IQQ", 3, 1, len(kv)) + b"".join(kv)
    tensor = _string("weight") + struct.pack("<I", 1) + struct.pack("<Q", elements) + struct.pack("<IQ", tensor_type, 0)
    body = header + tensor
    padding = (-len(body)) % 32
    return body + b"\0" * padding + b"\x01" * (elements * width)


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(role="hp", state_dir=tmp_path / "state", models_dir=tmp_path / "models",
                  projects_dir=tmp_path / "projects", config_dir=tmp_path / "config",
                  token="t" * 40)
    values.update(overrides)
    values["models_dir"].mkdir(parents=True, exist_ok=True)
    return Settings(**values)


class FakeTransport:
    """Stands in for HubTransport: records metadata calls and serves bytes."""
    def __init__(self, payload: bytes, *, sha256="auto", commit=COMMIT, size=None, on_chunk=None, chunk=64):
        self.payload = payload
        self.sha256 = hashlib.sha256(payload).hexdigest() if sha256 == "auto" else sha256
        self.commit = commit
        self.size = len(payload) if size is None else size
        self.on_chunk = on_chunk
        self.chunk = chunk
        self.metadata_calls = []

    def metadata(self, task):
        self.metadata_calls.append(dict(task))
        return {"commit": self.commit, "size": self.size, "sha256": self.sha256}

    def chunks(self, task):
        assert task["resolved_revision"] == self.commit
        for index in range(0, len(self.payload), self.chunk):
            if self.on_chunk:
                self.on_chunk(index)
            yield self.payload[index:index + self.chunk]


def staging_files(root: Path):
    folder = root / models.STAGING
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


# ---- GGUF structural inspection -------------------------------------------------

def test_inspect_complete_gguf_reports_metadata(tmp_path):
    path = write(tmp_path / "m.gguf", make_gguf(name="Tiny Test"))
    with path.open("rb") as stream:
        details = inspect_gguf(stream)
    assert details["gguf_version"] == 3
    assert details["tensor_count"] == 1
    assert details["metadata"] == {"name": "Tiny Test", "architecture": "llama", "quantization": 0,
                                   "split_count": 1, "split_index": 0}


@pytest.mark.parametrize("cut", [1, 64, 127])
def test_inspect_rejects_truncated_tensor_data(tmp_path, cut):
    data = make_gguf()
    path = write(tmp_path / "m.gguf", data[:-cut])
    with path.open("rb") as stream, pytest.raises(ValueError, match="incomplete"):
        inspect_gguf(stream)


@pytest.mark.parametrize("keep", [3, 20, 60])
def test_inspect_rejects_truncated_header(tmp_path, keep):
    path = write(tmp_path / "m.gguf", make_gguf()[:keep])
    with path.open("rb") as stream, pytest.raises(ValueError):
        inspect_gguf(stream)


def test_inspect_rejects_wrong_magic_and_unknown_tensor_type(tmp_path):
    bad = write(tmp_path / "bad.gguf", b"GGML" + make_gguf()[4:])
    with bad.open("rb") as stream, pytest.raises(ValueError, match="not a little-endian GGUF"):
        inspect_gguf(stream)
    unknown = write(tmp_path / "unknown.gguf", make_gguf(tensor_type=99))
    with unknown.open("rb") as stream, pytest.raises(ValueError, match="Unsupported GGUF tensor type"):
        inspect_gguf(stream)


def test_inspect_rejects_huge_metadata_length_without_reading_it(tmp_path):
    data = b"GGUF" + struct.pack("<IQQ", 3, 1, 1) + struct.pack("<Q", 2 ** 60)
    path = write(tmp_path / "huge.gguf", data)
    with path.open("rb") as stream, pytest.raises(ValueError):
        inspect_gguf(stream)


def test_inspect_quantized_block_size(tmp_path):
    # Q4_0 (type 2): 32 values per 18-byte block.
    complete = write(tmp_path / "q.gguf", make_gguf(tensor_type=2, elements=64, width=0) + b"\x00" * 36)
    with complete.open("rb") as stream:
        assert inspect_gguf(stream)["tensor_count"] == 1
    short = write(tmp_path / "short.gguf", complete.read_bytes()[:-1])
    with short.open("rb") as stream, pytest.raises(ValueError, match="incomplete"):
        inspect_gguf(stream)


# ---- Request validation and path confinement ---------------------------------

@pytest.mark.parametrize("payload", [
    {"repo_id": "bad repo", "filename": "a.gguf"},
    {"repo_id": "org/repo", "filename": "../a.gguf"},
    {"repo_id": "org/repo", "filename": "/abs/a.gguf"},
    {"repo_id": "org/repo", "filename": "dir\\a.gguf"},
    {"repo_id": "org/repo", "filename": "C:a.gguf"},
    {"repo_id": "org/repo", "filename": ".hidden.gguf"},
    {"repo_id": "org/repo", "filename": "a.safetensors"},
    {"repo_id": "org/repo", "filename": "a.gguf", "local_name": "sub/a.gguf"},
    {"repo_id": "org/repo", "filename": "a.gguf", "revision": "main\n"},
    {"repo_id": "org/repo", "filename": "a.gguf", "command": "rm -rf /"},
])
def test_download_request_rejects_unsafe_values(payload):
    with pytest.raises(ValueError):
        DownloadRequest(**payload)


def test_download_request_accepts_subfolder_filename():
    request = DownloadRequest(repo_id="org/repo", filename="q4/model-Q4_K_M.gguf")
    assert request.revision == "main" and request.local_name is None


@pytest.mark.parametrize("relative", ["../outside.gguf", "a/../../outside.gguf", "/etc/x.gguf", "a\\b.gguf"])
def test_safe_open_cannot_escape_root(tmp_path, relative):
    root = tmp_path / "models"
    root.mkdir()
    write(tmp_path / "outside.gguf", make_gguf())
    with pytest.raises(ValueError):
        with safe_open(root, relative):
            pass


def test_safe_open_refuses_symlink(tmp_path):
    root = tmp_path / "models"
    root.mkdir()
    target = write(tmp_path / "outside.gguf", make_gguf())
    try:
        (root / "link.gguf").symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation is not permitted on this host")
    with pytest.raises((ValueError, OSError)):
        with safe_open(root, "link.gguf"):
            pass


# ---- Catalog ------------------------------------------------------------------

def test_catalog_lists_only_complete_gguf(tmp_path):
    config = settings(tmp_path)
    root = config.models_dir
    write(root / "good.gguf", make_gguf(name="good"))
    write(root / "nested" / "Other.GGUF", make_gguf(name="nested"))
    write(root / "partial.gguf", make_gguf()[:-10])
    write(root / "notes.txt", b"hello")
    write(root / ".hidden.gguf", make_gguf())
    write(root / models.STAGING / "abc.partial", make_gguf())
    write(root / ".trash" / "old.gguf", make_gguf())
    library = ModelLibrary(config, transport=FakeTransport(b""))
    result = library.catalog()
    assert [item["id"] for item in result["items"]] == ["good.gguf", "nested/Other.GGUF"]
    assert result["items"][0]["metadata"]["name"] == "good"
    assert result["items"][0]["format"] == "GGUF"
    assert result["total_bytes"] == sum(item["size_bytes"] for item in result["items"])
    assert [warning["id"] for warning in result["warnings"]] == ["partial.gguf"]
    # A later scan uses the cache but still notices the file was completed.
    write(root / "partial.gguf", make_gguf())
    assert "partial.gguf" in [item["id"] for item in library.catalog()["items"]]


def test_catalog_reports_missing_models_root(tmp_path):
    config = settings(tmp_path)
    library = ModelLibrary(config, transport=FakeTransport(b""))
    config.models_dir.rmdir()
    with pytest.raises(HTTPException) as caught:
        library.catalog()
    assert caught.value.status_code == 503


def test_hash_requires_complete_gguf_inside_root(tmp_path):
    config = settings(tmp_path)
    data = make_gguf()
    write(config.models_dir / "m.gguf", data)
    write(config.models_dir / "bad.gguf", data[:-1])
    library = ModelLibrary(config, transport=FakeTransport(b""))
    assert library.hash("m.gguf")["sha256"] == hashlib.sha256(data).hexdigest()
    for model_id in ("bad.gguf", "../m.gguf", "missing.gguf"):
        with pytest.raises(HTTPException) as caught:
            library.hash(model_id)
        assert caught.value.status_code == 400


# ---- Download queue -------------------------------------------------------------

def enqueue(library, **values):
    values.setdefault("repo_id", "org/repo")
    values.setdefault("filename", "sub/model.gguf")
    return library.enqueue(DownloadRequest(**values))


def test_download_success_publishes_verified_file(tmp_path):
    config = settings(tmp_path)
    payload = make_gguf(name="downloaded")
    transport = FakeTransport(payload)
    library = ModelLibrary(config, transport=transport)
    task = enqueue(library, local_name="mine.gguf", revision="v1")
    assert task["state"] == "queued" and len(task["id"]) == 32
    assert library.run_next() is True
    done = library.get(task["id"])
    assert done["state"] == "completed", done
    assert done["resolved_revision"] == COMMIT
    assert done["sha256"] == hashlib.sha256(payload).hexdigest()
    assert done["progress"] == 100 and done["downloaded_bytes"] == len(payload)
    assert (config.models_dir / "mine.gguf").read_bytes() == payload
    assert staging_files(config.models_dir) == []
    assert transport.metadata_calls[0]["revision"] == "v1"
    item = library.catalog()["items"][0]
    assert item["id"] == "mine.gguf"
    assert item["source"] == {"repo_id": "org/repo", "filename": "sub/model.gguf", "revision": COMMIT}
    assert library.run_next() is False
    saved = json.loads((config.state_dir / "models" / "downloads.json").read_text(encoding="utf-8"))
    assert saved["items"][0]["state"] == "completed"


def test_download_without_hub_checksum_still_validates_size_and_format(tmp_path):
    config = settings(tmp_path)
    payload = make_gguf()
    library = ModelLibrary(config, transport=FakeTransport(payload, sha256=None))
    task = enqueue(library)
    library.run_next()
    assert library.get(task["id"])["state"] == "completed"
    assert (config.models_dir / "model.gguf").exists()


def test_download_checksum_mismatch_publishes_nothing(tmp_path):
    config = settings(tmp_path)
    library = ModelLibrary(config, transport=FakeTransport(make_gguf(), sha256="0" * 64))
    task = enqueue(library)
    library.run_next()
    failed = library.get(task["id"])
    assert failed["state"] == "failed"
    assert failed["error"] == "Download checksum did not match the Hub"
    assert not (config.models_dir / "model.gguf").exists()
    assert staging_files(config.models_dir) == []


def test_download_short_or_long_body_fails(tmp_path):
    config = settings(tmp_path)
    payload = make_gguf()
    short = ModelLibrary(config, transport=FakeTransport(payload[:-8], sha256=None, size=len(payload)))
    task = enqueue(short, local_name="short.gguf")
    short.run_next()
    assert "incomplete" in short.get(task["id"])["error"]
    long = ModelLibrary(config, transport=FakeTransport(payload + b"x" * 100, sha256=None, size=len(payload)))
    task = enqueue(long, local_name="long.gguf")
    long.run_next()
    assert "exceeded its advertised size" in long.get(task["id"])["error"]
    assert not any((config.models_dir / name).exists() for name in ("short.gguf", "long.gguf"))


def test_download_of_non_gguf_content_is_rejected_after_checksum(tmp_path):
    config = settings(tmp_path)
    payload = b"<html>not a model</html>" * 4
    library = ModelLibrary(config, transport=FakeTransport(payload))
    task = enqueue(library)
    library.run_next()
    failed = library.get(task["id"])
    assert failed["state"] == "failed" and "GGUF" in failed["error"]
    assert not (config.models_dir / "model.gguf").exists()


def test_cancel_queued_download(tmp_path):
    config = settings(tmp_path)
    transport = FakeTransport(make_gguf())
    library = ModelLibrary(config, transport=transport)
    task = enqueue(library)
    assert library.cancel(task["id"])["state"] == "cancelled"
    assert library.run_next() is False
    assert transport.metadata_calls == []
    # Cancelling a finished task leaves it unchanged.
    assert library.cancel(task["id"])["state"] == "cancelled"
    with pytest.raises(HTTPException) as caught:
        library.cancel("f" * 32)
    assert caught.value.status_code == 404


def test_cancel_during_download_discards_partial(tmp_path):
    config = settings(tmp_path)
    holder = {}

    def cancel_midway(index):
        if index == 128:
            assert library.get(holder["id"])["state"] == "downloading"
            library.cancel(holder["id"])

    library = ModelLibrary(config, transport=FakeTransport(make_gguf(), on_chunk=cancel_midway))
    holder["id"] = enqueue(library)["id"]
    library.run_next()
    task = library.get(holder["id"])
    assert task["state"] == "cancelled" and task["error"] == "Cancelled by user"
    assert not (config.models_dir / "model.gguf").exists()
    assert staging_files(config.models_dir) == []


def test_service_stop_requeues_download(tmp_path):
    config = settings(tmp_path)
    library = ModelLibrary(config, transport=FakeTransport(make_gguf(), on_chunk=lambda i: library.stopping.set()))
    task = enqueue(library)
    library.run_next()
    assert library.get(task["id"])["state"] == "queued"


def test_no_overwrite_existing_or_pending_names(tmp_path):
    config = settings(tmp_path)
    original = write(config.models_dir / "Existing.gguf", b"user data")
    library = ModelLibrary(config, transport=FakeTransport(make_gguf()))
    for name in ("Existing.gguf", "existing.gguf"):
        with pytest.raises(HTTPException) as caught:
            enqueue(library, local_name=name)
        assert caught.value.status_code == 409
    enqueue(library, local_name="pending.gguf")
    with pytest.raises(HTTPException) as caught:
        enqueue(library, local_name="PENDING.gguf")
    assert caught.value.status_code == 409
    assert original.read_bytes() == b"user data"


def test_destination_created_during_download_is_not_replaced(tmp_path):
    config = settings(tmp_path)
    target = config.models_dir / "race.gguf"

    def create_competitor(index):
        if index == 0:
            target.write_bytes(b"copied by a user over SMB")

    library = ModelLibrary(config, transport=FakeTransport(make_gguf(), on_chunk=create_competitor))
    task = enqueue(library, local_name="race.gguf")
    library.run_next()
    failed = library.get(task["id"])
    assert failed["state"] == "failed" and "left unchanged" in failed["error"]
    assert target.read_bytes() == b"copied by a user over SMB"
    assert staging_files(config.models_dir) == []


def test_downloads_disabled_by_configuration(tmp_path):
    library = ModelLibrary(settings(tmp_path, allow_network_downloads=False), transport=FakeTransport(b""))
    with pytest.raises(HTTPException) as caught:
        enqueue(library)
    assert caught.value.status_code == 403


def test_unexpected_transport_error_hides_details(tmp_path):
    class Leaky(FakeTransport):
        def metadata(self, task):
            raise RuntimeError("https://user:secret-token@example/signed?X-Amz-Signature=abc")

    config = settings(tmp_path)
    library = ModelLibrary(config, transport=Leaky(b""))
    task = enqueue(library)
    library.run_next()
    error = library.get(task["id"])["error"]
    assert "secret" not in error and "Signature" not in error
    assert error.startswith("Hub request failed")


def test_restart_requeues_interrupted_download_at_pinned_revision(tmp_path):
    config = settings(tmp_path)
    payload = make_gguf()
    library = ModelLibrary(config, transport=FakeTransport(payload))
    task = enqueue(library)
    library._update(task["id"], state="downloading", resolved_revision=COMMIT, downloaded_bytes=50, progress=10)
    cancelled = enqueue(library, local_name="other.gguf")
    library._update(cancelled["id"], state="downloading", cancel_requested=True)

    restarted = ModelLibrary(config, transport=FakeTransport(payload))
    recovered = restarted.get(task["id"])
    assert recovered["state"] == "queued" and recovered["downloaded_bytes"] == 0
    assert "Interrupted by restart" in recovered["error"]
    assert restarted.get(cancelled["id"])["state"] == "cancelled"
    restarted.run_next()
    assert restarted.get(task["id"])["state"] == "completed"
    assert restarted.transport.metadata_calls[0]["resolved_revision"] == COMMIT

    # If the Hub later resolves a different commit, the pinned task fails safely.
    again = enqueue(restarted, local_name="pinned.gguf")
    restarted._update(again["id"], resolved_revision="b" * 40)
    restarted.run_next()
    assert "different revision" in restarted.get(again["id"])["error"]


def test_restart_recovers_publish_that_happened_before_state_write(tmp_path):
    config = settings(tmp_path)
    payload = make_gguf()
    library = ModelLibrary(config, transport=FakeTransport(payload))
    task = enqueue(library)
    write(config.models_dir / "model.gguf", payload)
    library._update(task["id"], state="validating", resolved_revision=COMMIT, total_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest())
    restarted = ModelLibrary(config, transport=FakeTransport(b"never used"))
    restarted.run_next()
    assert restarted.get(task["id"])["state"] == "completed"
    assert restarted.transport.metadata_calls == []


def test_restart_does_not_adopt_a_different_file_at_the_destination(tmp_path):
    config = settings(tmp_path)
    payload = make_gguf()
    library = ModelLibrary(config, transport=FakeTransport(payload))
    task = enqueue(library)
    write(config.models_dir / "model.gguf", b"user copy, not a GGUF")
    library._update(task["id"], state="validating", resolved_revision=COMMIT, total_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest())
    restarted = ModelLibrary(config, transport=FakeTransport(payload))
    restarted.run_next()
    failed = restarted.get(task["id"])
    assert failed["state"] == "failed" and "left unchanged" in failed["error"]
    assert (config.models_dir / "model.gguf").read_bytes() == b"user copy, not a GGUF"


def test_corrupt_saved_state_is_refused(tmp_path):
    config = settings(tmp_path)
    state = config.state_dir / "models"
    state.mkdir(parents=True)
    (state / "downloads.json").write_text(json.dumps({"items": [{"id": "../x", "state": "queued"}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        ModelLibrary(config, transport=FakeTransport(b""))


# ---- Hugging Face transport with mocked HTTP --------------------------------------

def test_hub_metadata_validation(monkeypatch):
    replies = []

    def fake_metadata(url, **kwargs):
        replies.append((url, kwargs))
        return replies_value

    monkeypatch.setattr(models, "get_hf_file_metadata", fake_metadata)
    monkeypatch.setattr(models, "get_token", lambda: None)
    task = {"repo_id": "org/repo", "filename": "sub/m.gguf", "revision": "main", "resolved_revision": None}

    replies_value = SimpleNamespace(commit_hash=COMMIT, size=1000, etag='"' + "AB" * 32 + '"')
    result = models.HubTransport().metadata(task)
    assert result == {"commit": COMMIT, "size": 1000, "sha256": "ab" * 32}
    assert replies[0][0] == "https://huggingface.co/org/repo/resolve/main/sub/m.gguf"
    assert replies[0][1]["retry_on_errors"] is False

    replies_value = SimpleNamespace(commit_hash=COMMIT, size=1000, etag='"abc-git-sha1"')
    assert models.HubTransport().metadata(task)["sha256"] is None

    pinned = dict(task, resolved_revision=COMMIT)
    models.HubTransport().metadata(pinned)
    assert f"/resolve/{COMMIT}/" in replies[-1][0]

    for bad in (SimpleNamespace(commit_hash=None, size=1000, etag=""),
                SimpleNamespace(commit_hash="main", size=1000, etag=""),
                SimpleNamespace(commit_hash=COMMIT, size=None, etag=""),
                SimpleNamespace(commit_hash=COMMIT, size=10, etag=""),
                SimpleNamespace(commit_hash=COMMIT, size=models.MAX_DOWNLOAD_BYTES + 1, etag="")):
        replies_value = bad
        with pytest.raises(ValueError):
            models.HubTransport().metadata(task)


REAL_CLIENT = httpx.Client


def patch_httpx(monkeypatch, handler):
    def client(**kwargs):
        return REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(models.httpx, "Client", client)


def test_hub_chunks_streams_with_token_and_redirect(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "huggingface.co":
            return httpx.Response(302, headers={"Location": "https://cdn.example/blob"})
        return httpx.Response(200, content=b"x" * 3000)

    patch_httpx(monkeypatch, handler)
    monkeypatch.setattr(models, "get_token", lambda: "hf_secret")
    task = {"repo_id": "org/repo", "filename": "m.gguf", "resolved_revision": COMMIT}
    assert b"".join(models.HubTransport().chunks(task)) == b"x" * 3000
    assert str(seen[0].url) == f"https://huggingface.co/org/repo/resolve/{COMMIT}/m.gguf"
    assert seen[0].headers["authorization"] == "Bearer hf_secret"
    # The token is not forwarded to the redirected CDN host.
    assert "authorization" not in seen[1].headers
    assert seen[1].headers["accept-encoding"] == "identity"


def test_hub_chunks_refuses_http_redirect_and_errors(monkeypatch):
    def handler(request):
        if request.url.host == "huggingface.co":
            return httpx.Response(302, headers={"Location": "http://cdn.example/blob"})
        return httpx.Response(200, content=b"data")

    patch_httpx(monkeypatch, handler)
    monkeypatch.setattr(models, "get_token", lambda: None)
    task = {"repo_id": "org/repo", "filename": "m.gguf", "resolved_revision": COMMIT}
    with pytest.raises(ValueError, match="outside HTTPS"):
        list(models.HubTransport().chunks(task))

    patch_httpx(monkeypatch, lambda request: httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        list(models.HubTransport().chunks(task))


def test_http_status_error_becomes_safe_task_message(monkeypatch, tmp_path):
    config = settings(tmp_path)

    class Transport(FakeTransport):
        def chunks(self, task):
            request = httpx.Request("GET", "https://huggingface.co/x?token=secret")
            raise httpx.HTTPStatusError("secret", request=request, response=httpx.Response(403, request=request))
            yield b""

    library = ModelLibrary(config, transport=Transport(make_gguf()))
    task = enqueue(library)
    library.run_next()
    assert library.get(task["id"])["error"].startswith("Hub download returned HTTP 403")


# ---- HTTP API via FastAPI ---------------------------------------------------------

def test_api_routes_download_and_catalog(tmp_path, monkeypatch):
    config = settings(tmp_path)
    payload = make_gguf(name="api")
    monkeypatch.setattr(models, "HubTransport", lambda: FakeTransport(payload))
    app = FastAPI()
    app.include_router(models.create_router(config), prefix="/api")
    with TestClient(app) as client:
        assert client.get("/api/models").json()["items"] == []
        response = client.post("/api/downloads", json={"repo_id": "org/repo", "filename": "api.gguf", "revision": "main"})
        assert response.status_code == 202
        task_id = response.json()["id"]
        deadline = time.monotonic() + 10
        while client.get(f"/api/downloads/{task_id}").json()["state"] != "completed":
            assert time.monotonic() < deadline, client.get(f"/api/downloads/{task_id}").json()
            time.sleep(0.05)
        assert [item["id"] for item in client.post("/api/models/scan").json()["items"]] == ["api.gguf"]
        assert client.get("/api/downloads").json()["items"][0]["id"] == task_id
        assert client.post(f"/api/downloads/{task_id}/cancel").json()["state"] == "completed"
        assert client.get("/api/downloads/" + "0" * 32).status_code == 404
        assert client.post("/api/downloads", json={"repo_id": "org/repo", "filename": "../x.gguf"}).status_code == 422
        assert client.post("/api/downloads", json={"repo_id": "org/repo", "filename": "api.gguf"}).status_code == 409
        assert client.get("/api/models/hash", params={"model_id": "api.gguf"}).json()["sha256"] == hashlib.sha256(payload).hexdigest()


def test_router_is_empty_for_dell(tmp_path):
    router = models.create_router(settings(tmp_path, role="dell"))
    assert router.routes == []
