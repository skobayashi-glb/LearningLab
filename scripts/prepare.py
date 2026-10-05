"""Rebuild local C# hosts and effect-preview caches from the bundled sources."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
UTF8 = "utf-8"
JSON_INDENT = 2
BUILD_CONFIGURATION = "Release"
FRAMEWORK = "net10.0"
DEFAULT_WORKERS = 2
BUILD_TIMEOUT_SECONDS = 180
BAKE_TIMEOUT_SECONDS = 120
SUCCESS_EXIT_CODE = 0
PREFERRED_SURFACE_MODEL = "shark.json"


class DotnetHost:
    def __init__(self, project: Path, assembly: str):
        self.project = project
        self.assembly = project.parent / "bin" / BUILD_CONFIGURATION / FRAMEWORK / assembly

    def build(self) -> None:
        subprocess.run(["dotnet", "build", str(self.project), "-c", BUILD_CONFIGURATION,
                        "--nologo"], cwd=ROOT, check=True, timeout=BUILD_TIMEOUT_SECONDS)


class EffectCache:
    def __init__(self, host: DotnetHost):
        self.host = host
        self.specs = ROOT / "Assets/Resources/VoxelEffects"
        self.models = ROOT / "Assets/Resources/VoxelCharacterModels"
        self.output = ROOT / "EffectStudio/out"

    def bake(self, spec: Path) -> dict:
        target = self.output / (spec.stem + ".frames.json")
        dependencies = [self.host.assembly, *self.specs.glob("*.json")]
        latest_input = max(path.stat().st_mtime for path in dependencies)
        if target.exists() and target.stat().st_mtime >= latest_input:
            return {"name": spec.stem, "status": "cached"}
        document = json.loads(spec.read_text(encoding=UTF8))
        command = ["dotnet", str(self.host.assembly), str(spec), str(target)]
        if document.get("effectType") == "surfaceTransition" or document.get("surfaceTransitionType"):
            model = self.models / PREFERRED_SURFACE_MODEL
            if not model.exists():
                model = next(iter(sorted(self.models.glob("*.json"))), None)
            if model is None:
                raise FileNotFoundError("Surface-transition preview requires a bundled character model")
            command.extend(["--surface", str(model)])
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                encoding=UTF8, errors="replace", timeout=BAKE_TIMEOUT_SECONDS)
        if result.returncode != SUCCESS_EXIT_CODE:
            raise RuntimeError(f"{spec.name}: {result.stderr.strip() or result.stdout.strip()}")
        return {"name": spec.stem, "status": "baked"}

    def prepare(self, workers: int, only: set[str]) -> list[dict]:
        self.output.mkdir(parents=True, exist_ok=True)
        specs = sorted(self.specs.glob("*.json"))
        if only:
            known = {spec.stem for spec in specs}
            missing = only - known
            if missing:
                raise FileNotFoundError(f"Unknown effects: {sorted(missing)}")
            specs = [spec for spec in specs if spec.stem in only]
        with ThreadPoolExecutor(max_workers=workers) as executor:
            return list(executor.map(self.bake, specs))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only-effects", nargs="*", default=[])
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--build-only", action="store_true")
    arguments = parser.parse_args()
    configuration = json.loads((ROOT / "repository.json").read_text(encoding=UTF8))
    if configuration["kind"] == "learning":
        DotnetHost(ROOT / "LearningLab/BattleHost/BattleHost.csproj", "BattleHost.dll").build()
    elif configuration["name"] in {"MotionStudio", "EffectStudio"}:
        host = DotnetHost(ROOT / "EffectStudio/Baker/EffectBaker.csproj", "EffectBaker.dll")
        host.build()
        if not arguments.build_only:
            reports = EffectCache(host).prepare(arguments.workers, set(arguments.only_effects))
            print(json.dumps({"effects": len(reports), "baked": sum(
                report["status"] == "baked" for report in reports)}, indent=JSON_INDENT))
    else:
        print("No build preparation required.")


if __name__ == "__main__":
    main()
