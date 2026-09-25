# Model library and downloads (HP)

The HP dashboard lists the GGUF model files on the NAS share and can download new GGUF files from Hugging Face. It lists only **complete** GGUF files. A file still being copied, cut short, or holding unknown tensor types appears under **warnings** with a reason instead. File size tells you how big the download is. It does **not** tell you whether the model fits in a PC's RAM or VRAM.

## What gets listed

- Files ending in `.gguf` (any capitalization) anywhere under `models_dir`, the NAS models folder.
- The app checks each file's GGUF v2/v3 header and confirms that every tensor's data is actually present. It reads the header only, never the model weights, and never runs the model.
- It skips hidden names (starting with `.`), the private `.homelab-downloads` staging folder, symbolic links, and Windows junctions.
- A model ID is its path relative to the NAS models folder, such as `qwen/model-Q4_K_M.gguf`. IDs that are absolute or use `..` or backslashes are refused.
- Split models (`-00001-of-00003.gguf`) are listed part by part. Each part reports its `split_index` and `split_count`.

## Downloads

Enter the repository (`owner/name`), the file path inside it, and a revision (default `main`). You can also give a local filename. Downloads run one at a time in a persistent queue.

1. The app asks Hugging Face for the file size, the exact commit, and the SHA-256 checksum when the Hub publishes one (it does for LFS files). The task stays pinned to that commit, even after a restart.
2. It checks there is enough free space for the file plus 256 MiB.
3. It downloads into the hidden `.homelab-downloads` folder on the same NAS filesystem. Only `https://` redirects are followed. Your Hugging Face token (from the service account's normal `hf` login, if any) goes only to `huggingface.co`, never to the redirect host.
4. The byte count must match exactly, and so must the checksum when one exists. The file must also pass the GGUF completeness check.
5. The app publishes the file with a hard link, which cannot replace an existing file. If a file with the same name appears meanwhile (ignoring capitalization, as SMB clients do), the download fails and your file is left untouched.

**Cancel** stops a queued or running download and deletes the partial file. If the service stops mid-download, the task goes back to the queue and restarts from byte zero at the saved commit. Error messages never include signed URLs, tokens, or raw network errors.

A local administrator can set `allow_network_downloads: false` in `/etc/homelab/apps.json` to turn off downloads. The library listing still works.

## API reference

All routes are under `/api`, need the normal login, and exist only on the HP.

| Route | Result |
| --- | --- |
| `GET /models`, `POST /models/scan` | `{items, total_bytes, warnings}`. Each item: `id`, `name`, `size_bytes`, `modified_at`, `format: "GGUF"`, `gguf_version`, `tensor_count`, `metadata` (`name`, `architecture`, `quantization` = GGUF file-type number, `split_count`, `split_index`), `source` (repo, filename, commit for files this app downloaded, else null). |
| `GET /models/hash?model_id=` | `{id, sha256, size_bytes}` for a complete GGUF. Reads the whole file. |
| `POST /downloads` | Body `{repo_id, filename, revision?, local_name?}`. Returns the task with status 202. |
| `GET /downloads` | `{items}` newest first. |
| `GET /downloads/{id}` | Task: `state` (`queued`, `resolving`, `downloading`, `validating`, `completed`, `failed`, `cancelled`), `downloaded_bytes`, `total_bytes`, `progress`, `resolved_revision`, `sha256`, `model_id`, `error`. |
| `POST /downloads/{id}/cancel` | Requests cancellation. Finished tasks are returned unchanged. |

Errors: 422 for an invalid repository or filename, or unknown fields. 409 when the local name exists or is already pending (ignoring capitalization), or when history reaches 1000 entries. 403 when downloads are disabled. 503 when the NAS folder is missing or is a symbolic link (mount the storage first). 404 for an unknown task.

## Tests

```
PYTHONPATH=services .venv/Scripts/python.exe -m pytest services/tests/test_models.py
```

(On Linux use `.venv/bin/python`.) The tests build tiny GGUF files in temporary folders and use a fake Hub transport plus mocked HTTP responses. They never download a real model or run inference. The Linux-only descriptor-relative staging path (`O_NOFOLLOW`, `dir_fd`, and group ownership) is exercised only on the real HP.
