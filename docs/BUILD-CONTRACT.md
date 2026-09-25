# Application implementation contract

The deployment app is a Python 3.12 FastAPI package at `services/homelab`.
It runs as an unprivileged `homelab` system account on Ubuntu 24.04, outside
Docker so the Dell does not grant a web app access to the Docker socket.
Docker/Portainer remain available for user containers. HP listens on its
selected LAN IP at 8088; Dell workbench on its selected LAN IP at 8090.

## Shared configuration

`homelab.config.Settings` is a dataclass with these fields (all Paths unless
noted): `role` (str hp/dell), `state_dir`, `models_dir`, `projects_dir`,
`config_dir`, `hp_url` (str), `dell_url` (str), `token` (str),
`allow_network_downloads` (bool, default true).
Configuration comes from `/etc/homelab/apps.json`, with `--config` override.
Root creates directories. Modules may create children in `state_dir`.

## Integration

Each feature exports `create_router(settings: Settings) -> APIRouter`.
Root app mounts routers under `/api`. Authentication is enforced centrally
for all `/api` routes except login. A bearer token or same-origin HttpOnly
session cookie is required. Never include credentials in JSON status output.
Routes are role-specific; unsupported roles must not expose features.

- HP module `models.py`: `/models`, `/models/scan`, `/downloads` POST/GET,
  `/downloads/{id}` GET, `/downloads/{id}/cancel` POST. Model IDs are relative
  paths; paths cannot escape the NAS root. Only complete GGUF files appear.
  Download requests use repo_id, filename, revision, optional local_name;
  return task ID and state. Models list returns `{items:[...]}`; task lists
  likewise. JSON errors use FastAPI HTTPException.
- Dell module `workbench.py`: `/projects` GET/POST, `/jobs` GET/POST,
  `/jobs/{id}` GET, `/jobs/{id}/cancel` POST, `/search` GET,
  `/search/reindex` POST, `/files` GET. Project-create supports named local
  Python templates and optional HTTPS GitHub repository import. Jobs are
  explicit configured presets, never a shell command submitted to the API.
  Results contain bounded logs. Search/read are confined to registered
  project roots. `mcp_server.py` exposes concrete tools through this API on
  loopback 8765. Never log the API token. No arbitrary shell MCP tool.
- Both-role module `operations.py`: `/metrics` GET, `/backups` GET,
  `/backups/run` POST, `/backups/check` POST, `/backups/restore` POST,
  `/backups/tasks/{id}` GET. Restic plans live in root-owned config_dir,
  API can select plans but cannot edit commands/destinations. Restore goes
  to a NEW subfolder of state_dir/restores. System metrics use psutil;
  optional Cisco SNMP monitoring is installed separately, no invented data.

Root owns app.py/config.py, browser UI, service installer, Windows control
app, integration tests and final guide. Feature owners add focused tests in
`services/tests`, and a short feature reference under docs/features. Coordinate
schema changes before writing the UI. No agent edits another owner's files.

## Limits

No scripts erase arrays, format disks, change current laptop AI configuration,
or run inference/recording checks on the active user machine. All test data is
temporary. Real hardware acceptance remains a deployment step. The UI must
say unavailable when Dell/clients are off. Model file size is not a promise
of RAM/VRAM fit. Backup status distinguishes no backup plan from success.
