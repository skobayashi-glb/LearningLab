"""Read-only export of saved Unity StageDefinition and AllyData assets."""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

import yaml

from .artifacts import write_json
from .battle import HOST_DLL
from .config import ExperimentConfig

PROJECT = Path(__file__).resolve().parents[2]
ASSETS = PROJECT / "Assets"
DEFAULT_CELL_SIZE = 3.2
TERRAIN_BYTES = 4
BASE_NAMES = ("SubBaseData", "MainBaseData", "EliteBaseData")


def read_unity(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    text = re.sub(r"^%.*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^--- !u!\d+ &[-\d]+.*$", "---", text, flags=re.MULTILINE)
    # Unity stores enum arrays as little-endian hex scalars. Preserve leading zeros.
    text = re.sub(r"^(\s*(?:terrainCells|appliedStatuses):)[ \t]*([0-9a-fA-F]*)[ \t]*$",
                  lambda match: match[1] + " '" + match[2] + "'", text, flags=re.MULTILINE)
    documents = list(yaml.safe_load_all(text))
    behaviors = [doc["MonoBehaviour"] for doc in documents if doc and "MonoBehaviour" in doc]
    if len(behaviors) != 1:
        raise ValueError(f"{path}: expected one ScriptableObject")
    return behaviors[0]


def enum_array(value) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        return [int(v) for v in value]
    raw = bytes.fromhex(value)
    if len(raw) % TERRAIN_BYTES:
        raise ValueError("Malformed Unity enum array")
    return [int.from_bytes(raw[i:i + TERRAIN_BYTES], "little", signed=True)
            for i in range(0, len(raw), TERRAIN_BYTES)]


def snake_fields(data):
    if isinstance(data, dict):
        return {re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower(): snake_fields(v) for k, v in data.items()}
    if isinstance(data, list):
        return [snake_fields(v) for v in data]
    return data


def rotate(offset, facing: int) -> list[int]:
    x, y = offset["x"], offset["y"]
    for _ in range(facing):
        x, y = y, -x
    return [x, y]


class BattleExporter:
    def __init__(self, project: Path = PROJECT):
        self.project = project.resolve()
        self.assets = self.project / "Assets"
        self.sources = {}
        self.characters = {}
        for path in (self.assets / "ScriptableObjects/AllyData").rglob("*.asset"):
            data = read_unity(path)
            if data.get("modelName"):
                self.characters[data["modelName"]] = (path, data)

    def source(self, path: Path) -> None:
        self.sources[str(path.relative_to(self.project))] = hashlib.sha256(path.read_bytes()).hexdigest()

    def character(self, character_id: str) -> dict:
        if character_id not in self.characters:
            raise ValueError(f"Unknown AllyData modelName: {character_id}")
        path, data = self.characters[character_id]
        self.source(path)
        if data.get("moveType", 0) != 0 or data.get("targetSelection", 0) != 0:
            raise ValueError(f"{character_id}: movement/target selection is not supported yet")
        if data.get("positionEffect", {}).get("kind", 0) != 0 or data.get("knockbackHpRatios"):
            raise ValueError(f"{character_id}: position effects/knockback require parity implementation")
        # Support-role behavior is not silently converted into normal attacks.
        role_path = self.assets / "Scripts/Battle/UnitRole.cs"
        if role_path.exists():
            role_text = role_path.read_text(encoding="utf-8-sig")
            for role_name in ("Healer", "Buffer"):
                match = re.search(role_name + r"\s*=\s*1\s*<<\s*(\d+)", role_text)
                if match and data.get("role", 0) & (1 << int(match[1])):
                    raise ValueError(f"{character_id}: {role_name} not supported")
        motion_path = self.assets / "Resources/CharacterMotions" / (character_id + ".json")
        if not motion_path.exists():
            raise ValueError(f"{character_id}: missing authoritative motion timing")
        self.source(motion_path)
        motion = json.loads(motion_path.read_text(encoding="utf-8-sig"))
        triggers = motion["technique"]["effectTriggerSeconds"]
        impact = triggers[0]
        recovery = motion["clips"]["attack"]["duration"] - impact
        if impact <= 0 or recovery <= 0:
            raise ValueError(f"{character_id}: invalid motion attack timing")
        engagement = copy.deepcopy(data["engagementRange"])
        if not data.get("useEngagementRangeOverride"):
            engagement["sectorRadius"] = data["attackRange"]
        return {
            "id": character_id, "hp": data["maxHp"], "power": data["attackPower"],
            "defense": data.get("defense", 0),
            "cost": data["deployCost"], "speed": data["moveSpeed"],
            "impact_seconds": impact, "recovery_seconds": recovery,
            "range": data["attackRange"], "lunge": data.get("attackLunge", 0),
            "ranged": data.get("attackApproach", 0) == 1,
            "ignore_sight": data.get("attackLineOfSight", 0) == 1,
            "move_type": data.get("moveType", 0), "target_selection": data.get("targetSelection", 0),
            "max_targets": data.get("maxTargets", 0),
            "body": data["overrideBodyBox"] if data.get("useBodyBoxOverride") else data["generatedBodyBox"],
            "shape": snake_fields(data["attackShape"]), "engagement": snake_fields(engagement),
            "statuses": enum_array(data.get("appliedStatuses")),
            "knockback_hp_ratios": [], "source_hash": self.sources[str(path.relative_to(self.project))]}

    def stage(self, entry: dict) -> dict:
        path = (self.project / entry["stage_asset"]).resolve()
        if not path.is_relative_to(self.project):
            raise ValueError("stage_asset must be inside the project")
        data = read_unity(path)
        self.source(path)
        if data.get("contentKind", 0) != 0 or not data.get("hasPlayerBase"):
            raise ValueError("A combat stage with a player base is required")
        terrain = enum_array(data["terrainCells"])
        if len(terrain) != data["width"] * data["height"]:
            raise ValueError("Stage terrain size mismatch")
        # The main footprint is explicit in the scenario instead of guessing prefab data.
        main = entry["main_base"]
        position = data["playerBasePosition"]
        facing = data.get("playerBaseFacing", 0)

        def absolute(offsets, pos, turn):
            result = []
            for offset in offsets:
                x, y = rotate(offset, turn)
                result.append([pos["x"] + x, pos["y"] + y])
            return result

        bases = [{"id": "main", "team": 0, "main": True, "boss": False,
                  "hp": main["hp"], "cell": [position["x"], position["y"]],
                  "footprint": absolute(main["footprint"], position, facing),
                  "spawn_cells": absolute(main["spawn_slots"], position, facing),
                  "garrison": main.get("garrison", [])}]
        boss_index = entry.get("boss_base_index")
        if boss_index is None:
            indices = [i for i, b in enumerate(data["enemyBases"]) if b["type"] == 1]
            if len(indices) != 1:
                raise ValueError("Specify boss_base_index for stages without exactly one main enemy base")
            boss_index = indices[0]
        if not 0 <= boss_index < len(data["enemyBases"]):
            raise ValueError("Invalid boss_base_index")
        for index, placement in enumerate(data["enemyBases"]):
            definition_path = self.assets / "ScriptableObjects/EnamyBaseData" / (
                BASE_NAMES[placement["type"]] + ".asset")
            definition = read_unity(definition_path)
            self.source(definition_path)
            overrides = entry.get("enemy_bases", {}).get(str(index), {})
            pos = placement["position"]
            turn = placement.get("facing", 0)
            bases.append({
                "id": f"enemy-{index}", "team": 1, "main": False, "boss": index == boss_index,
                "hp": overrides.get("hp", definition["maxHp"]),
                "cell": [pos["x"], pos["y"]],
                "footprint": absolute(definition["footprint"], pos, turn),
                "spawn_cells": absolute(definition["spawnSlots"], pos, turn),
                "garrison": overrides.get("garrison", [])})
        return {"id": data["stageId"], "width": data["width"], "height": data["height"],
                "terrain": terrain, "generation_seed": data["generationSeed"],
                "wall_candidate_path_rate": data["wallCandidatePathRate"], "bases": bases}

    def export(self, setup: dict) -> dict:
        config = copy.deepcopy(setup["training"])
        scenarios = []
        for entry in setup["scenarios"]:
            battle = {"version": 1, "cell_size": setup.get("cell_size", DEFAULT_CELL_SIZE),
                      "stage": self.stage(entry), "rules": entry["rules"],
                      "roster": entry["roster"], "enemy_pool": entry["enemy_pool"],
                      "initial_units": entry.get("initial_units", [])}
            ids = set(entry["roster"]) | set(entry["enemy_pool"])
            ids.update(u["character"] for u in battle["initial_units"])
            ids.update(c for b in battle["stage"]["bases"] for c in b["garrison"])
            baseline = [self.character(id) for id in sorted(ids)]
            battle["enemy_baseline"] = copy.deepcopy(baseline)
            battle["ally_definitions"] = copy.deepcopy(baseline)
            allowed = {"hp", "power", "speed", "cost", "defense"}
            for unit in battle["ally_definitions"]:
                changes = entry.get("ally_overrides", {}).get(unit["id"], {})
                if set(changes) - allowed:
                    raise ValueError("Only hp/power/speed/cost/defense can be overridden")
                unit.update(changes)
            statuses = []
            for path in sorted((self.assets / "ScriptableObjects/StatusDefinitions").glob("*.asset")):
                definition = read_unity(path)
                if "statusId" in definition:
                    self.source(path); statuses.append(definition)
            battle["status_definitions"] = statuses
            reaction_path = self.assets / "Resources/StatusReactionCatalog.asset"
            settings_path = self.assets / "Resources/StatusCatalog.asset"
            self.source(reaction_path); self.source(settings_path)
            battle["reaction_definitions"] = read_unity(reaction_path)["definitions"]
            battle["status_settings"] = {k: v for k, v in read_unity(settings_path).items()
                                         if isinstance(v, (int, float))}
            scenarios.append({"id": entry["id"], "family": entry["family"],
                              "split": entry["split"], "weight": entry.get("weight", 1),
                              "parameters": {"battle": battle}})
        if not HOST_DLL.exists():
            raise ValueError("Build BattleHost before exporting")
        config["environment_factory"] = "learning_lab.battle:BattleEnvironment"
        config["environment_options"] = {
            **setup["envelope"], "engine_sha256": hashlib.sha256(HOST_DLL.read_bytes()).hexdigest()}
        config["scenarios"] = scenarios
        config["source_assets"] = self.sources
        ExperimentConfig(config)
        return config


def main():
    parser = argparse.ArgumentParser(description="Freeze editor stages and ally/enemy snapshots")
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ASSETS.resolve()):
        raise ValueError("Write experiments outside Assets")
    if args.output.exists():
        raise FileExistsError("Use a new output name to preserve experiment snapshots")
    setup = json.loads(args.setup.read_text(encoding="utf-8-sig"))
    config = BattleExporter().export(setup)
    environment = ExperimentConfig(config).create_environment()
    try:
        for scenario in config["scenarios"]:
            environment.reset(scenario, config["seed"])
    finally:
        environment.close()
    write_json(args.output, config)
    print(f"Exported {len(config['scenarios'])} scenarios: {args.output}")


if __name__ == "__main__":
    main()
