"""Copy explicitly selected integration data to a Unity project, preserving its metadata."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
UTF8 = "utf-8"
JSON_INDENT = 2
COMPLETE_MOTION_STATUS = "complete"
CHARACTER_OUTPUT_DIRECTORIES = ("Assets/Resources/CharacterMotions",
                                "Assets/Resources/VoxelCharacterModels",
                                "Assets/Resources/CharacterCatalog")
EXPORT_PATHS = {
    "SA-maker": [("SA-maker/export", "SA-maker/export"),
                 ("SA-maker/unity", "Assets/Editor/SA-maker")],
    "SAStudio": [("SA-maker/export", "SA-maker/export"), ("Assets/SA", "Assets/SA"),
                 ("Assets/Scripts/SpriteAnim", "Assets/Scripts/SpriteAnim")],
    "VoxelStudio": [("Assets/Resources/RockTiles", "Assets/Resources/RockTiles"),
                    ("Assets/GeneratedVoxelModels", "Assets/GeneratedVoxelModels"),
                    ("Assets/VoxelModels", "Assets/VoxelModels"),
                    ("Assets/Sprites/VoxelStudio", "Assets/Sprites/VoxelStudio")],
    "MotionStudio": [("Assets/Resources/StructureMotions", "Assets/Resources/StructureMotions"),
                     ("Assets/Resources/VoxelEffects", "Assets/Resources/VoxelEffects"),
                     ("Assets/Resources/VoxelCloneSequences", "Assets/Resources/VoxelCloneSequences")],
    "EffectStudio": [("Assets/Resources/VoxelEffects", "Assets/Resources/VoxelEffects"),
                     ("Assets/Scripts/Effects/Core", "Assets/Scripts/Effects/Core"),
                     ("Assets/Scripts/CharacterVisuals/VoxelCharacterModelDocument.cs",
                      "Assets/Scripts/CharacterVisuals/VoxelCharacterModelDocument.cs")],
    "UIStudio": [("Assets/UI/Sprites", "Assets/UI/Sprites"),
                 ("Assets/Editor/UIStudioTextureProcessor.cs", "Assets/Editor/UIStudioTextureProcessor.cs"),
                 ("Assets/Editor/UIStudioPrefabBuilder.cs", "Assets/Editor/UIStudioPrefabBuilder.cs")],
    "LearningLab": [],
    "TowerDefense": [],
}


class UnityExport:
    def __init__(self, target: Path):
        self.target = target.resolve()
        if not (self.target / "ProjectSettings/ProjectVersion.txt").is_file():
            raise ValueError("--project must be an existing Unity project's root")
        if self.target == ROOT or self.target.is_relative_to(ROOT):
            raise ValueError("Choose a separate Unity project for export")

    def plan(self, name: str, characters: list[str] | None = None) -> list[tuple[Path, Path]]:
        pairs = []
        for source_name, target_name in EXPORT_PATHS[name]:
            source = ROOT / source_name
            target = self.target / target_name
            if not source.exists():
                continue
            files = sorted(source.rglob("*")) if source.is_dir() else [source]
            for path in files:
                if not path.is_file() or path.suffix == ".meta":
                    continue
                destination = target / path.relative_to(source) if source.is_dir() else target
                if not destination.resolve().is_relative_to(self.target):
                    raise ValueError(f"Export path escapes project: {destination}")
                pairs.append((path, destination))
                meta = Path(str(path) + ".meta")
                target_meta = Path(str(destination) + ".meta")
                if meta.is_file() and not target_meta.exists():
                    pairs.append((meta, target_meta))
        if name == "MotionStudio":
            statuses = json.loads((ROOT / "MotionStudio/motion_status.json").read_text(encoding=UTF8))["statuses"]
            names = sorted(name for name, status in statuses.items() if status == COMPLETE_MOTION_STATUS)
            if characters:
                unknown = sorted(set(characters) - set(names))
                if unknown:
                    raise ValueError(f"Characters must have complete status: {', '.join(unknown)}")
                names = sorted(set(characters))
            for directory in CHARACTER_OUTPUT_DIRECTORIES:
                for character in names:
                    relative = Path(directory) / (character + ".json")
                    destination = self.target / relative
                    if not destination.resolve().is_relative_to(self.target):
                        raise ValueError(f"Character path escapes project: {character}")
                    pairs.append((ROOT / relative, destination))
        return pairs

    @staticmethod
    def apply(pairs: list[tuple[Path, Path]]) -> None:
        for source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--characters", nargs="+", help="MotionStudio only: export selected complete characters")
    arguments = parser.parse_args()
    name = json.loads((ROOT / "repository.json").read_text(encoding=UTF8))["name"]
    if arguments.characters and name != "MotionStudio":
        parser.error("--characters is supported only by MotionStudio")
    exporter = UnityExport(arguments.project)
    pairs = exporter.plan(name, arguments.characters)
    if not arguments.dry_run:
        if name == "MotionStudio":
            command = ["node", "Tools/sync-character-catalog.js"]
            if arguments.characters:
                command.append("--names=" + ",".join(arguments.characters))
            subprocess.run(command, cwd=ROOT, check=True)
        exporter.apply(pairs)
    print(json.dumps({"repository": name, "dry_run": arguments.dry_run,
                      "files": [str(target) for _, target in pairs]},
                     ensure_ascii=False, indent=JSON_INDENT))


if __name__ == "__main__":
    main()
