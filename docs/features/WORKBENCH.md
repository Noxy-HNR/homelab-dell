# Developer workbench and MCP tools (Dell)

The Dell workbench keeps Python projects in `projects_dir`. It runs named jobs (tests or compile checks), searches project text, and shows files. An AI client can use the same features through MCP tools on the Dell.

## Projects

- **Python template:** creates `app.py`, `tests/test_app.py`, `README.md`, `.gitignore`, and `pyproject.toml`. Its test job passes right away.
- **GitHub import:** accepts exactly `https://github.com/OWNER/REPOSITORY` (optionally ending in `.git`). URLs containing credentials, ports, extra paths, queries, SSH, or other hosts are refused. The clone is shallow, without submodules or hooks, with file and ext protocols disabled, no password prompt, and a 90-second limit. A repository containing symbolic links, or more than 50,000 entries, is refused and removed. Private repositories are not supported.
- Names use lowercase letters, digits, and hyphens (48 characters maximum). An existing folder is never adopted or overwritten.

## Jobs

A job is a named preset. It is never a command typed into the API. The built-in presets are:

| Preset | Runs | Limit |
| --- | --- | --- |
| `test` | `python -m unittest discover -s tests` | 15 min |
| `compile` | `python -m compileall -q .` | 15 min |

A local administrator can add presets in a root-owned, not group/world-writable `config_dir/workbench.json`:

```json
{"presets": {"lint": {"args": ["-m", "compileall", "-q", "src"], "timeout_seconds": 600}}}
```

`args` are arguments to the service's Python only (at most 32). The limit is 1–3600 seconds. Jobs run one at a time, in the project folder, as the `homelab` service account. They get a minimal environment (`PATH`, locale, and temp variables only). Up to 20 jobs can wait in the queue. Output keeps the last 128 KiB and says `log_truncated: true` when older lines were dropped.

States: `queued`, `running`, `succeeded`, `failed`, `cancelled`, `timed_out`, `interrupted`. Cancel stops the whole process group. A service restart marks a running job **interrupted** and continues with the queued ones.

**Jobs execute the project's own code.** Only run projects you trust. A job runs as the same account as the web service, so it could read what that account can read.

## Search and file view

**Reindex** reads UTF-8 text files up to 256 KiB each, at most 2,000 files per project. It skips `.git`, virtual environments, `node_modules`, build and cache folders, symbolic links, binary files, and credential-like names (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `credentials.*`, `secrets.*`). Search treats your text as a phrase, not query syntax. Results are checked again at search time, so a deleted file or one replaced by a symbolic link disappears even before the next reindex. File reads use the same rules. Paths must be relative, use forward slashes, stay inside the project, and cannot pass through a symbolic link.

## API reference

All routes are under `/api`, need the normal login, and exist only on the Dell.

| Route | Result |
| --- | --- |
| `GET /projects` | `{items: [{id, name, source, created_at, presets}]}` |
| `POST /projects` | Body `{name, template: "python"}` or `{name, git_url}`. 201 with the project. |
| `GET /jobs` | `{items}`: the 100 most recent, newest first. |
| `POST /jobs` | Body `{project_id, preset}`. 202 with the job. |
| `GET /jobs/{id}` | `{id, project_id, preset, state, exit_code, log, log_truncated, created_at, started_at, finished_at, timeout_seconds}` |
| `POST /jobs/{id}/cancel` | Cancels a queued or running job. Finished jobs are returned unchanged. |
| `GET /search?q=&project_id=` | `{items: [{project_id, path, snippet}]}`, at most 30. |
| `POST /search/reindex` | Body `{project_id?}` → `{indexed_files, skipped_files}` |
| `GET /files?project_id=&path=` | `{project_id, path, content, truncated}` |

Errors: 400 for bad names, URLs, paths, or presets. 403 for excluded or credential paths, or for GitHub import when `allow_network_downloads` is false. 404 for missing items. 409 for a name in use or a project folder that is missing or linked. 413 for oversized files or trees. 415 for binary or non-UTF-8 files. 429 when the queue is full. 503 or 504 when Git is missing or times out.

## MCP tools

The `homelab-mcp` service runs Streamable HTTP at `http://127.0.0.1:8765/mcp` on the Dell. It is loopback only; reach it through the SSH tunnel in [DELL.md](../setup/DELL.md). Every request needs `Authorization: Bearer <Dell token>`. The tools call the workbench API above with that token. There is no shell or file-writing tool.

Tools: `list_projects`, `search_project_text`, `read_project_file`, `list_jobs`, `get_job`, `start_project_job`, `run_project_tests`, `compile_project`, `cancel_job`, `reindex_projects`. Upstream error bodies are replaced by a short message with the HTTP status, so paths and tokens do not reach the AI client. Requests with a non-local `Host` header are refused.

## Tests

```
PYTHONPATH=services .venv/Scripts/python.exe -m pytest services/tests/test_workbench.py services/tests/test_mcp.py
```

They create template projects in temporary folders and run real `python` subprocess jobs (test, compile, cancel, timeout, restart). GitHub cloning is mocked; the tests check the hardened `git` arguments but do not reach GitHub. The MCP tests use a mocked upstream, the real workbench router over an in-process transport, and a real Uvicorn MCP server on a random loopback port.
