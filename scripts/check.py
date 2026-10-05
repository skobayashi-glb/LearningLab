"""Check standalone dependencies and preservation of the initial source snapshot."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
UTF8 = "utf-8"
JSON_INDENT = 2
SUCCESS_EXIT_CODE = 0
FAILURE_EXIT_CODE = 1
HASH_BUFFER_BYTES = 1024 * 1024
CONFIGURATION = json.loads((ROOT / "repository.json").read_text(encoding=UTF8))
manifest = json.loads((ROOT / "SOURCE_MANIFEST.json").read_text(encoding=UTF8))
missing = []
modified = []
unresolved = []
for entry in manifest["files"]:
    source = ROOT / entry["source"]
    if not source.is_file():
        missing.append(entry["source"])
        continue
    hasher = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(HASH_BUFFER_BYTES), b""):
            hasher.update(block)
    if hasher.hexdigest() != entry["sha256"]:
        modified.append(entry["source"])
for project in ROOT.rglob("*.csproj"):
    for element in ET.parse(project).findall(".//Compile"):
        include = element.get("Include", "").replace("\\", "/")
        if not include:
            continue
        if "*" in include:
            matches = list(project.parent.glob(include))
        else:
            matches = [(project.parent / include).resolve()]
        if not matches or any(not candidate.is_file() or not candidate.is_relative_to(ROOT)
                              for candidate in matches):
            unresolved.append(f"{project.relative_to(ROOT)}: {include}")
report = {"repository": CONFIGURATION["name"], "source_files": len(manifest["files"]),
          "missing": missing, "unresolved_csharp_sources": unresolved,
          "edited_since_snapshot": modified, "passed": not missing and not unresolved}
print(json.dumps(report, ensure_ascii=False, indent=JSON_INDENT))
raise SystemExit(SUCCESS_EXIT_CODE if report["passed"] else FAILURE_EXIT_CODE)
