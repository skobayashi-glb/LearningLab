import copy
import json
from pathlib import Path
import tempfile
import unittest

from learning_lab.balance import BalanceSearch, ParameterSpace, apply_candidate
from learning_lab.config import ExperimentConfig, canonical_hash
from test_battle import battle_scenario

LAB = Path(__file__).resolve().parents[1]


def fixture():
    config = json.loads((LAB / "configs/demo.json").read_text())
    config["environment_factory"] = "learning_lab.battle:BattleEnvironment"
    config["environment_options"] = {"width": 12, "height": 7}
    config["scenarios"] = []
    for split in ("train", "validation", "holdout"):
        scenario = battle_scenario()
        scenario.update(id=split, family=split, split=split)
        config["scenarios"].append(scenario)
    settings = {
        "version": 2, "candidate_budget": 3, "search_seed": 12, "change_weight": 0.01,
        "seeds": {"train": [1], "validation": [2], "holdout": [3]},
        "parameters": [{"character": "fighter", "field": "hp", "values": [10, 20, 30]}],
        "cases": [{"id": "tank", "character": "fighter", "role": "tank", "reference_cost": 2,
                   "weight": 1, "roster": ["fighter"],
                   "targets": {"survival_fraction": {"min": 0.5, "max": 1, "scale": 1, "weight": 1}}}]}
    return ExperimentConfig(config), settings


class BalanceTests(unittest.TestCase):
    def test_candidates_include_baseline_are_unique_and_bounded(self):
        config, settings = fixture()
        space = ParameterSpace(config, settings)
        candidates = list(space.candidates(10, 3))
        self.assertEqual(candidates[0], {"fighter": {"hp": 20}})
        self.assertEqual({c["fighter"]["hp"] for c in candidates}, {10, 20, 30})
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates, list(space.candidates(10, 3)))

    def test_candidate_does_not_mutate_enemy_or_source(self):
        config, _ = fixture()
        before = config.digest
        changed = apply_candidate(config, {"fighter": {"hp": 30}})
        for original, candidate in zip(config.data["scenarios"], changed.data["scenarios"]):
            a, b = original["parameters"]["battle"], candidate["parameters"]["battle"]
            self.assertEqual(canonical_hash(a["enemy_baseline"]), canonical_hash(b["enemy_baseline"]))
            self.assertEqual(b["ally_definitions"][0]["hp"], 30)
        self.assertEqual(config.digest, before)

    def test_defense_search_accepts_zero_and_uses_finite_change_scale(self):
        config, settings = fixture()
        for scenario in config.data["scenarios"]:
            scenario["parameters"]["battle"]["ally_definitions"][0]["defense"] = 0
        settings["parameters"] = [{"character": "fighter", "field": "defense", "values": [0, 50, 100]}]
        space = ParameterSpace(config, settings)
        self.assertEqual(space.change_penalty({"fighter": {"defense": 100}}), 1)
        self.assertEqual(space.change_penalty({"fighter": {"defense": 0}}), 0)

    def test_rejects_missing_baseline_and_seed_leakage(self):
        config, settings = fixture()
        missing = copy.deepcopy(settings)
        missing["parameters"][0]["values"] = [10, 30]
        with self.assertRaises(ValueError):
            BalanceSearch(config, missing)
        settings["seeds"]["holdout"] = settings["seeds"]["train"]
        with self.assertRaises(ValueError):
            BalanceSearch(config, settings)

    def test_validation_rejects_overfit_candidate_and_holdout_is_not_searched(self):
        config, settings = fixture()
        search = BalanceSearch(config, settings)
        calls = []

        def evaluate(candidate, split):
            hp = candidate["fighter"]["hp"]
            calls.append((hp, split))
            loss = abs(hp - 30) if split == "train" else abs(hp - 20)
            return {"split": split, "loss": loss, "cases": []}

        search.evaluator.evaluate = evaluate
        with tempfile.TemporaryDirectory() as directory:
            report = search.run(Path(directory))
            self.assertFalse(report["passed_validation_gate"])
            self.assertEqual(report["recommended_overrides"], {"fighter": {"hp": 20}})
            self.assertEqual([c for c in calls if c[1] == "holdout"], [(20, "holdout")])
            self.assertTrue((Path(directory) / "recommended-config.json").exists())

    def test_improved_candidate_is_exported_without_applying_to_game(self):
        config, settings = fixture()
        search = BalanceSearch(config, settings)
        search.evaluator.evaluate = lambda candidate, split: {
            "split": split, "loss": abs(candidate["fighter"]["hp"] - 30), "cases": []}
        with tempfile.TemporaryDirectory() as directory:
            report = search.run(Path(directory))
            self.assertTrue(report["passed_validation_gate"])
            self.assertEqual(report["recommended_overrides"], {"fighter": {"hp": 30}})
            self.assertFalse(report["applied_to_game"])

    def test_aggregate_improvement_cannot_hide_a_case_regression(self):
        config, settings = fixture()
        search = BalanceSearch(config, settings)

        def evaluate(candidate, split):
            baseline = candidate["fighter"]["hp"] == 20
            if split == "train":
                return {"split": split, "loss": abs(candidate["fighter"]["hp"] - 30), "cases": []}
            return {"split": split, "loss": 10 if baseline else 2, "cases": [
                {"case": "attacker", "loss": 20 if baseline else 0},
                {"case": "tank", "loss": 0 if baseline else 4}]}

        search.evaluator.evaluate = evaluate
        with tempfile.TemporaryDirectory() as directory:
            report = search.run(Path(directory))
            self.assertFalse(report["passed_validation_gate"])
            self.assertEqual(report["recommended_overrides"], {"fighter": {"hp": 20}})

    def test_joint_regression_rejects_individually_good_candidate_before_holdout(self):
        config, settings = fixture()
        settings["cases"][0]["freeze_others"] = True
        search = BalanceSearch(config, settings)
        calls = []

        def evaluate(candidate, split, *, joint=False):
            hp = candidate["fighter"]["hp"]
            calls.append((hp, split, joint))
            loss = abs(hp - 30) if not joint else abs(hp - 20) + 20
            return {"split": split, "loss": loss, "cases": [{"case": "tank", "loss": loss}]}

        search.evaluator.evaluate = evaluate
        with tempfile.TemporaryDirectory() as directory:
            report = search.run(Path(directory))
        self.assertTrue(report["passed_individual_validation"])
        self.assertFalse(report["passed_validation_gate"])
        self.assertEqual(calls[-1], (20, "holdout", True))
        self.assertEqual(calls[-2], (30, "validation", True))
