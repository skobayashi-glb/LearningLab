"""Static guard only. A Unity player build is still the final release verification."""
import json
import re
from pathlib import Path

LAB_DIRECTORY = "LearningLab"
RUNTIME_SOURCE_SUFFIXES = {".cs", ".asmdef", ".asmref"}
PACKAGE_FILES = ("manifest.json", "packages-lock.json")
FORBIDDEN_REFERENCES = ("learninglab", "learning_lab")
EDITOR_DIRECTORY = "editor"


def find_violations(project: Path) -> list[str]:
    project = project.resolve()
    lab = (project / LAB_DIRECTORY).resolve()
    assets = project / "Assets"
    violations = []
    catalog = assets / "Resources/StageCatalog.asset"
    if catalog.exists():
        catalog_text = catalog.read_text(encoding="utf-8-sig")
        for meta in (assets / "Editor").rglob("*.asset.meta"):
            match = re.search(r"^guid:\s*(\w+)", meta.read_text(encoding="utf-8-sig"), re.MULTILINE)
            if match and match[1] in catalog_text:
                violations.append(f"StageCatalog references editor-only asset: {meta}")
    if lab.is_relative_to(assets.resolve()):
        violations.append("LearningLab must be outside Assets")
    sources = []
    for path in assets.rglob("*"):
        if path.is_symlink() and path.resolve().is_relative_to(lab):
            violations.append(f"Assets links to learning tools: {path}")
        if path.is_file() and path.suffix.lower() in RUNTIME_SOURCE_SUFFIXES:
            # Editor-only dependencies are allowed at the export boundary.
            if EDITOR_DIRECTORY not in [part.lower() for part in path.relative_to(assets).parts]:
                sources.append(path)
    sources.extend(project / "Packages" / name for name in PACKAGE_FILES)
    for path in sources:
        if path.exists():
            content = path.read_text(encoding="utf-8-sig").lower()
            if any(token in content for token in FORBIDDEN_REFERENCES):
                violations.append(f"Runtime/package references learning tools: {path}")
    return violations


if __name__ == "__main__":
    problems = find_violations(Path(__file__).resolve().parent.parent)
    print(json.dumps({"passed": not problems, "violations": problems},
                     ensure_ascii=False, indent=2))
    raise SystemExit(bool(problems))
