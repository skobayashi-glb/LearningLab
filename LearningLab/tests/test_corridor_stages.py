import copy
import json
from pathlib import Path
import unittest

from learning_lab.battle import HOST_DLL
from learning_lab.config import ExperimentConfig, ScenarioSampler, SHARED_LAYOUTS
from learning_lab.export_battle import BattleExporter, read_unity
from learning_lab.evaluation import PolicyEvaluator

LAB = Path(__file__).resolve().parents[1]
PROJECT = LAB.parent
SIZE = 15
START = 2
END = 12
MID_ROW = SIZE // 2
PATH = 0
WALL = 2
STEP = 1
RIGHT = (STEP, 0)
UP = (0, STEP)
STRAIGHT = "RL_Straight"
STAIRCASE = "RL_UnitStaircase"
EXPECTED_MAPS = {STRAIGHT, STAIRCASE}
SETUP_NAMES = ("battle_setup.json", "defense_training_stages.json")
PPO_SEED = 31415
SAMPLE_COUNT = 32


class CorridorGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.setup = json.loads((LAB / "configs/battle_setup.json").read_text())
        cls.exporter = BattleExporter()
        cls.stages = {cls.exporter.stage(entry)["id"]: cls.exporter.stage(entry)
                      for entry in cls.setup["scenarios"] if entry["split"] == "train"}

    def walkable(self, stage):
        occupied = {tuple(cell) for base in stage["bases"] for cell in base["footprint"]}
        return {(index % stage["width"], index // stage["width"])
                for index, terrain in enumerate(stage["terrain"])
                if terrain == PATH and (index % stage["width"], index // stage["width"]) not in occupied}

    def test_every_current_setup_uses_only_two_exact_maps_in_every_split(self):
        for filename in SETUP_NAMES:
            setup = json.loads((LAB / "configs" / filename).read_text())
            self.assertEqual(setup["training"]["stage_split_mode"], SHARED_LAYOUTS)
            self.assertEqual((setup["envelope"]["width"], setup["envelope"]["height"]), (SIZE, SIZE))
            for split in ("train", "validation", "holdout"):
                entries = [entry for entry in setup["scenarios"] if entry["split"] == split]
                self.assertEqual(len(entries), len(EXPECTED_MAPS))
                self.assertEqual({Path(entry["stage_asset"]).stem for entry in entries}, EXPECTED_MAPS)

    def test_straight_is_a_single_cell_wide_horizontal_corridor(self):
        stage = self.stages[STRAIGHT]
        self.assertEqual(self.walkable(stage), {(x, MID_ROW) for x in range(START + STEP, END)})
        self.assertTrue(set(stage["terrain"]).issubset({PATH, WALL}))

    def test_staircase_alternates_exactly_one_cell_right_and_one_cell_up(self):
        stage = self.stages[STAIRCASE]
        path = [(START, START)]
        for _ in range(END - START):
            for dx, dy in (RIGHT, UP):
                x, y = path[-1]
                path.append((x + dx, y + dy))
        self.assertEqual(self.walkable(stage), set(path[STEP:-STEP]))
        self.assertTrue(all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == STEP
                            for a, b in zip(path, path[STEP:])))
        self.assertTrue(set(stage["terrain"]).issubset({PATH, WALL}))

    def test_no_branch_shortcut_or_disconnected_walkable_cells(self):
        for stage in self.stages.values():
            cells = self.walkable(stage)
            degrees = {cell: sum((cell[0] + dx, cell[1] + dy) in cells
                                for dx, dy in (RIGHT, UP, (-STEP, 0), (0, -STEP))) for cell in cells}
            self.assertEqual(sum(degree == STEP for degree in degrees.values()), len(stage["bases"]))
            self.assertTrue(all(degree in (STEP, len(stage["bases"])) for degree in degrees.values()))
            seen = {next(iter(cells))}
            pending = list(seen)
            while pending:
                x, y = pending.pop()
                for dx, dy in (RIGHT, UP, (-STEP, 0), (0, -STEP)):
                    neighbor = (x + dx, y + dy)
                    if neighbor in cells and neighbor not in seen:
                        seen.add(neighbor)
                        pending.append(neighbor)
            self.assertEqual(seen, cells)

    def test_spawn_cells_and_unity_victory_target_match_corridor_endpoints(self):
        for entry in self.setup["scenarios"]:
            stage = self.exporter.stage(entry)
            data = read_unity(PROJECT / entry["stage_asset"])
            boss = next(base for base in stage["bases"] if base["boss"])
            target = data["victorySettings"]["bossBasePosition"]
            self.assertEqual(boss["cell"], [target["x"], target["y"]])
            walkable = self.walkable(stage)
            for base in stage["bases"]:
                self.assertEqual(len(base["spawn_cells"]), STEP)
                self.assertTrue(all(tuple(cell) in walkable for cell in base["spawn_cells"]))


@unittest.skipUnless(HOST_DLL.exists(), "Build BattleHost first")
class SharedLayoutSplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup = json.loads((LAB / "configs/battle_setup.json").read_text())
        cls.config = ExperimentConfig(BattleExporter().export(setup))

    def test_same_map_and_parameters_are_shared_without_renaming_families(self):
        families = {scenario["family"] for scenario in self.config.scenarios("train")}
        for split in ("validation", "holdout"):
            self.assertEqual({s["family"] for s in self.config.scenarios(split)}, families)
        self.assertTrue(self.config.reserved_evaluation_seeds)
        self.assertFalse(set(self.config.evaluation_seeds("validation")) & set(self.config.evaluation_seeds("holdout")))

    def test_shared_layout_is_an_explicit_opt_in(self):
        data = copy.deepcopy(self.config.data)
        data.pop("stage_split_mode")
        with self.assertRaisesRegex(ValueError, "stage family"):
            ExperimentConfig(data)

    def test_changed_layout_or_rules_cannot_masquerade_as_same_family(self):
        data = copy.deepcopy(self.config.data)
        validation = next(s for s in data["scenarios"] if s["split"] == "validation")
        validation["parameters"]["battle"]["rules"]["initial_energy"] += STEP
        with self.assertRaisesRegex(ValueError, "identical parameters"):
            ExperimentConfig(data)

    def test_missing_family_and_overlapping_evaluation_seeds_are_rejected(self):
        data = copy.deepcopy(self.config.data)
        data["scenarios"] = [s for s in data["scenarios"]
                             if not (s["family"] == "straight" and s["split"] == "holdout")]
        with self.assertRaisesRegex(ValueError, "same layout families"):
            ExperimentConfig(data)
        data = copy.deepcopy(self.config.data)
        data["evaluation"]["split_seeds"]["holdout"] = data["evaluation"]["split_seeds"]["validation"]
        with self.assertRaisesRegex(ValueError, "disjoint"):
            ExperimentConfig(data)

    def test_training_sampler_excludes_reserved_seeds_and_still_replays(self):
        initial = ScenarioSampler(self.config.scenarios("train"), PPO_SEED)
        first_seed = initial.next()[1]
        sampler = ScenarioSampler(self.config.scenarios("train"), PPO_SEED, excluded_seeds=[first_seed])
        replay = ScenarioSampler(self.config.scenarios("train"), PPO_SEED, excluded_seeds=[first_seed])
        for _ in range(SAMPLE_COUNT):
            scenario, seed = sampler.next()
            expected_scenario, expected_seed = replay.next()
            self.assertNotEqual(seed, first_seed)
            self.assertEqual((scenario["id"], seed), (expected_scenario["id"], expected_seed))

    def test_evaluator_uses_split_specific_seeds_for_both_layouts(self):
        config = ExperimentConfig(copy.deepcopy(self.config.data))
        for scenario in config.data["scenarios"]:
            scenario["parameters"]["battle"]["rules"]["episode_seconds"] = STEP
        for split in ("validation", "holdout"):
            report = PolicyEvaluator(config, None).evaluate(split, "first-legal")
            self.assertEqual(report["stage_split_mode"], SHARED_LAYOUTS)
            self.assertEqual(report["episode_count"], len(EXPECTED_MAPS) * len(config.evaluation_seeds(split)))
            self.assertEqual({e["seed"] for e in report["episodes"]}, set(config.evaluation_seeds(split)))
            self.assertEqual({e["scenario_id"] for e in report["episodes"]},
                             {s["id"] for s in config.scenarios(split)})


if __name__ == "__main__":
    unittest.main()
