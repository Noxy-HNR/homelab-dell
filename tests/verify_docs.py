"""Check local Markdown links, examples, diagrams and ignored local config paths."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

for path in ROOT.rglob("*.md"):
    if ".git" in path.parts:
        continue
    body = path.read_text(encoding="utf-8")
    assert body.count("```") % 2 == 0, f"Unclosed code fence: {path}"
    for link in re.findall(r"!?(?:\[[^\]]*\])\(([^)]+)\)", body):
        if link.startswith(("http:", "https:", "mailto:", "#")):
            continue
        target = link.split("#", 1)[0]
        assert (path.parent / target).exists(), f"Broken local link: {path}: {link}"

for path in ROOT.rglob("*.example.json"):
    json.loads(path.read_text(encoding="utf-8"))
for path in ROOT.rglob("*.svg"):
    ET.parse(path)

example = json.loads((ROOT / "client/game-drive/windows-client.example.json").read_text(encoding="utf-8"))
assert example["MinimumLinkSpeedGbps"] == 10
assert example["ControllerSshUser"].startswith("gamehandoff-")

for private_path in ("hp/iscsi/controller.json", "client/game-drive/game-client.json"):
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", private_path], cwd=ROOT, check=False
    )
    assert ignored.returncode == 0, f"Git must ignore {private_path}"
print("PASS: local links, JSON, SVG and ignored client config")
