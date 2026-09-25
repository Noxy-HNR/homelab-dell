"""MCP tool tests with a mocked upstream, the real workbench API, and real MCP HTTP."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket
import threading
import time

from fastapi import FastAPI
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import pytest
import uvicorn

from homelab import workbench as wb_module
from homelab.config import Settings
from homelab.mcp_server import WorkbenchAPI, create_http_app, create_mcp

TOKEN = "k" * 48
EXPECTED_TOOLS = {"list_projects", "search_project_text", "read_project_file", "list_jobs",
                  "start_project_job", "run_project_tests", "compile_project", "get_job",
                  "cancel_job", "reindex_projects"}


def settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(role="dell", state_dir=tmp_path / "state", models_dir=tmp_path / "models",
                  projects_dir=tmp_path / "projects", config_dir=tmp_path / "config",
                  dell_url="http://192.0.2.10:8090", token=TOKEN)
    values.update(overrides)
    return Settings(**values)


class Upstream:
    """Mock Dell API recording each request."""
    def __init__(self, status=200, body=None):
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = {"ok": True} if body is None else body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    @property
    def transport(self):
        return httpx.MockTransport(self)


def call(server, name, arguments=None):
    return asyncio.run(server.call_tool(name, arguments or {}))


def structured(result):
    # Plain dict tools have no output schema, so FastMCP returns one JSON text block.
    blocks = result[0] if isinstance(result, tuple) else result
    [block] = blocks
    return json.loads(block.text)


def test_tool_set_has_no_shell_or_filesystem_write(tmp_path):
    server = create_mcp(settings(tmp_path), Upstream().transport)
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert not {"command", "cmd", "shell", "args", "script"} & set(tool.inputSchema.get("properties", {}))
        assert tool.annotations.destructiveHint is False
    read_only = {tool.name for tool in tools if tool.annotations.readOnlyHint}
    assert read_only == {"list_projects", "search_project_text", "read_project_file", "list_jobs", "get_job"}


@pytest.mark.parametrize("tool, arguments, method, path, params, body", [
    ("list_projects", {}, "GET", "/api/projects", {}, None),
    ("search_project_text", {"query": "def main"}, "GET", "/api/search", {"q": "def main"}, None),
    ("search_project_text", {"query": "x", "project_id": "demo"}, "GET", "/api/search", {"q": "x", "project_id": "demo"}, None),
    ("read_project_file", {"project_id": "demo", "path": "src/a b.py"}, "GET", "/api/files", {"project_id": "demo", "path": "src/a b.py"}, None),
    ("list_jobs", {}, "GET", "/api/jobs", {}, None),
    ("start_project_job", {"project_id": "demo"}, "POST", "/api/jobs", {}, {"project_id": "demo", "preset": "test"}),
    ("start_project_job", {"project_id": "demo", "preset": "lint"}, "POST", "/api/jobs", {}, {"project_id": "demo", "preset": "lint"}),
    ("run_project_tests", {"project_id": "demo"}, "POST", "/api/jobs", {}, {"project_id": "demo", "preset": "test"}),
    ("compile_project", {"project_id": "demo"}, "POST", "/api/jobs", {}, {"project_id": "demo", "preset": "compile"}),
    ("get_job", {"job_id": "abc123"}, "GET", "/api/jobs/abc123", {}, None),
    ("cancel_job", {"job_id": "abc123"}, "POST", "/api/jobs/abc123/cancel", {}, None),
    ("reindex_projects", {}, "POST", "/api/search/reindex", {}, {"project_id": None}),
    ("reindex_projects", {"project_id": "demo"}, "POST", "/api/search/reindex", {}, {"project_id": "demo"}),
])
def test_tools_map_to_fixed_api_calls(tmp_path, tool, arguments, method, path, params, body):
    upstream = Upstream(body={"items": [1]})
    server = create_mcp(settings(tmp_path), upstream.transport)
    result = call(server, tool, arguments)
    assert structured(result) == {"items": [1]}
    [request] = upstream.requests
    assert request.method == method
    assert request.url.host == "192.0.2.10" and request.url.port == 8090
    assert request.url.path == path
    assert dict(request.url.params) == params
    assert request.headers["authorization"] == "Bearer " + TOKEN
    assert (json.loads(request.content) if request.content else None) == body


def test_job_id_cannot_traverse_api_paths(tmp_path):
    upstream = Upstream()
    server = create_mcp(settings(tmp_path), upstream.transport)
    call(server, "get_job", {"job_id": "../projects"})
    call(server, "cancel_job", {"job_id": "x/../../other?y=1"})
    assert upstream.requests[0].url.raw_path == b"/api/jobs/..%2Fprojects"
    assert upstream.requests[1].url.raw_path.startswith(b"/api/jobs/x%2F..%2F..%2Fother%3Fy%3D1/cancel")
    assert all(not request.url.params for request in upstream.requests)


def test_upstream_errors_do_not_leak_body_or_token(tmp_path):
    upstream = Upstream(status=500, body={"detail": "/srv/secret/path token=" + TOKEN})
    server = create_mcp(settings(tmp_path), upstream.transport)
    with pytest.raises(Exception) as caught:
        call(server, "list_projects")
    message = str(caught.value)
    assert "HTTP 500" in message
    assert "/srv/secret" not in message and TOKEN not in message

    redirect = httpx.MockTransport(lambda request: httpx.Response(302, headers={"Location": "http://evil.example/"}))
    with pytest.raises(Exception, match="HTTP 302"):
        call(create_mcp(settings(tmp_path), redirect), "list_projects")

    def offline(request):
        raise httpx.ConnectError("connection refused to " + TOKEN)

    with pytest.raises(Exception) as caught:
        call(create_mcp(settings(tmp_path), httpx.MockTransport(offline)), "list_projects")
    assert "unavailable" in str(caught.value) and TOKEN not in str(caught.value)


@pytest.mark.parametrize("overrides", [{"role": "hp"}, {"dell_url": ""}, {"token": "short"}])
def test_requires_dell_settings_and_token(tmp_path, overrides):
    with pytest.raises(ValueError):
        WorkbenchAPI(settings(tmp_path, **overrides))


def test_mcp_against_real_workbench_api(tmp_path):
    """MCP -> httpx ASGI transport -> the real workbench router and project files."""
    config = settings(tmp_path)
    api = FastAPI()
    api.include_router(wb_module.create_router(config), prefix="/api")
    server = create_mcp(config, httpx.ASGITransport(app=api))
    bench = wb_module.Workbench(config)
    bench.create_project(wb_module.ProjectCreate(name="demo", template="python"))
    (tmp_path / "projects" / "demo" / ".env").write_text("SECRET=1\n", encoding="utf-8")

    assert [p["id"] for p in structured(call(server, "list_projects"))["items"]] == ["demo"]
    assert structured(call(server, "reindex_projects"))["indexed_files"] == 5
    hits = structured(call(server, "search_project_text", {"query": "greet", "project_id": "demo"}))["items"]
    assert "app.py" in {hit["path"] for hit in hits}
    content = structured(call(server, "read_project_file", {"project_id": "demo", "path": "app.py"}))["content"]
    assert "def greet" in content
    for path in ("../demo/app.py", ".env", str(tmp_path / "projects" / "demo" / "app.py")):
        with pytest.raises(Exception, match="HTTP 40"):
            call(server, "read_project_file", {"project_id": "demo", "path": path})
    # Queue (worker not started, so nothing executes) and cancel through MCP.
    job = structured(call(server, "run_project_tests", {"project_id": "demo"}))
    assert job["state"] == "queued"
    assert structured(call(server, "cancel_job", {"job_id": job["id"]}))["state"] == "cancelled"
    assert structured(call(server, "get_job", {"job_id": job["id"]}))["state"] == "cancelled"
    with pytest.raises(Exception, match="HTTP 400"):
        call(server, "start_project_job", {"project_id": "demo", "preset": "rm -rf"})


# ---- Real streamable HTTP over loopback -------------------------------------------

@pytest.fixture
def live_mcp(tmp_path):
    upstream = Upstream(body={"items": [{"id": "demo"}]})
    app = create_http_app(settings(tmp_path), upstream.transport)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False, lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "MCP server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp", upstream
    server.should_exit = True
    thread.join(timeout=10)


def test_live_http_requires_bearer_token(live_mcp):
    url, upstream = live_mcp
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream"}
    for auth in (None, "Bearer wrong", "Bearer " + TOKEN + "x", TOKEN):
        extra = {"Authorization": auth} if auth else {}
        response = httpx.post(url, json=message, headers={**headers, **extra}, trust_env=False)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert TOKEN not in response.text
    assert upstream.requests == []


def test_live_http_rejects_foreign_host_header(live_mcp):
    url, _ = live_mcp
    response = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                          headers={"Authorization": "Bearer " + TOKEN, "Host": "attacker.example",
                                   "Accept": "application/json, text/event-stream"}, trust_env=False)
    assert response.status_code in (400, 403, 421)


def test_live_http_session_lists_and_invokes_tools(live_mcp):
    url, upstream = live_mcp

    async def session():
        http = httpx.AsyncClient(headers={"Authorization": "Bearer " + TOKEN}, trust_env=False, timeout=30)
        async with http, streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as client:
                initialized = await client.initialize()
                tools = await client.list_tools()
                result = await client.call_tool("list_projects", {})
                missing = await client.call_tool("run_shell", {"command": "id"})
                return initialized, tools, result, missing

    initialized, tools, result, missing = asyncio.run(session())
    assert initialized.serverInfo.name == "Homelab Workbench"
    assert "untrusted data" in initialized.instructions
    assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS
    assert result.isError is False
    assert json.loads(result.content[0].text) == {"items": [{"id": "demo"}]}
    assert missing.isError is True
    assert [request.url.path for request in upstream.requests] == ["/api/projects"]
