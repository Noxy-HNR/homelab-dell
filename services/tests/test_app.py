import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from homelab.app import create_app
from homelab.config import Settings


def settings(tmp_path: Path, role="hp"):
    for name in ("state", "models", "projects", "config"):
        (tmp_path / name).mkdir()
    return Settings(role, tmp_path / "state", tmp_path / "models", tmp_path / "projects",
                    tmp_path / "config", "http://127.0.0.1:18088", "http://127.0.0.1:18090",
                    "test-token-which-is-longer-than-thirty-two-characters", False)


def test_login_cookie_and_request_boundaries(tmp_path):
    s=settings(tmp_path)
    with TestClient(create_app(s)) as client:
        assert client.get("/healthz").json()=={"ok":True,"role":"hp"}
        assert client.get("/api/status").status_code==401
        assert client.post("/api/login",json={"token":s.token}).status_code==403
        headers={"X-Homelab-Request":"1"}
        assert client.post("/api/login",headers={**headers,"Origin":"http://evil.invalid"},json={"token":s.token}).status_code==403
        assert client.post("/api/login",headers=headers,json={"token":"wrong"}).status_code==401
        result=client.post("/api/login",headers=headers,json={"token":s.token})
        assert result.status_code==200
        assert "httponly" in result.headers["set-cookie"].lower()
        assert client.get("/api/status").json()["role"]=="hp"
        assert client.get("/api/models").json()["items"]==[]
        assert client.get("/api/projects").status_code==404
        assert client.get("/api/backups").json()["plans"]==[]
        assert client.get("/api/metrics").json()["current"]["memory"]["total"]>0
        assert client.post("/api/logout",headers=headers,json={}).status_code==200
        assert client.get("/api/status").status_code==401
        assert client.get("/api/status",headers={"Authorization":"Bearer "+s.token}).status_code==200


def test_real_project_job_search_workflow(tmp_path):
    s=settings(tmp_path,"dell")
    with TestClient(create_app(s),headers={"Authorization":"Bearer "+s.token}) as client:
        response=client.post("/api/projects",json={"name":"hello-lab","template":"python"})
        assert response.status_code in (200,201),response.text
        project=response.json()
        assert project["id"]=="hello-lab"
        assert client.get("/api/models").status_code==404
        job=client.post("/api/jobs",json={"project_id":project["id"],"preset":"test"}).json()
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            status=client.get("/api/jobs/"+job["id"]).json()
            if status["state"] not in ("queued","running"):
                break
            time.sleep(.1)
        assert status["state"] in ("succeeded","completed"),status
        assert status["exit_code"]==0
        indexed=client.post("/api/search/reindex",json={})
        assert indexed.status_code==200,indexed.text
        assert indexed.json()["indexed_files"]>0
        assert client.get("/api/files",params={"project_id":project["id"],"path":"../config/apps.json"}).status_code in (400,403)


def test_static_assets_and_no_token_in_status(tmp_path):
    s=settings(tmp_path)
    with TestClient(create_app(s),headers={"Authorization":"Bearer "+s.token}) as client:
        assert client.get("/").status_code==200
        assert client.get("/static/app.js").status_code==200
        status=client.get("/api/status")
        assert s.token not in status.text
        assert "frame-ancestors 'none'" in status.headers["content-security-policy"]


def test_login_rate_limit(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        for _ in range(10):
            assert client.post("/api/login",headers={"X-Homelab-Request":"1"},json={"token":"no"}).status_code==401
        assert client.post("/api/login",headers={"X-Homelab-Request":"1"},json={"token":"no"}).status_code==429
