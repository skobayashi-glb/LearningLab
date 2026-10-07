import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

from learning_lab.balance import ScriptedSortiePolicy, GLOBAL_FEATURES, PARTY_FEATURES, prepare_case, apply_candidate
from learning_lab.battle import HOST_DLL
from learning_lab.campaign import build_campaign, design_markdown, apply_strength_assignments, DEFAULT_STRENGTH_ASSIGNMENTS
from learning_lab.config import ExperimentConfig

LAB = Path(__file__).resolve().parents[1]


class CampaignPolicyTests(unittest.TestCase):
    def test_user_strength_file_is_authoritative_without_changing_parameters(self):
        plan = json.loads((LAB / "configs/character_balance_plan.json").read_text(encoding="utf-8"))
        strengths = json.loads(DEFAULT_STRENGTH_ASSIGNMENTS.read_text(encoding="utf-8"))
        strengths["assignments"]["shark"] = "weak"
        changed = apply_strength_assignments(plan, strengths)
        source = next(p for p in plan["profiles"] if p["id"] == "shark")
        result = next(p for p in changed["profiles"] if p["id"] == "shark")
        self.assertEqual(result["strength"], "weak")
        self.assertEqual(result["cost"], source["cost"])
        self.assertEqual(result["power"], source["power"])
        self.assertNotIn("strength", source)
        strengths["assignments"]["shark"] = "invalid"
        with self.assertRaises(ValueError):
            apply_strength_assignments(plan, strengths)

    def test_target_first_saves_energy_and_then_reverts_to_fixed_order(self):
        spec = SimpleNamespace(action_names=("wait", "deploy:0", "deploy:1"))
        values = np.zeros(GLOBAL_FEATURES + PARTY_FEATURES * 2, dtype=np.float32)
        values[1] = 0.1
        values[GLOBAL_FEATURES:GLOBAL_FEATURES + 3] = [1, 0.5, 0.1]
        values[GLOBAL_FEATURES + PARTY_FEATURES:GLOBAL_FEATURES + PARTY_FEATURES + 3] = [1, 1, 0.2]
        obs = SimpleNamespace(values=values, action_mask=np.array([True, True, False]))
        policy = ScriptedSortiePolicy(spec, 2, required_first_character=1)
        self.assertEqual(policy.choose(obs), 0)  # preserve energy for target, not cheap anchor
        values[1] = 0.3
        obs.action_mask[2] = True
        self.assertEqual(policy.choose(obs), 2)
        self.assertEqual(policy.choose(obs), 1)
        self.assertEqual(policy.choose(obs), 2)


@unittest.skipUnless(HOST_DLL.exists(), "Build BattleHost first")
class CampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((LAB / "configs/character_balance_plan.json").read_text(encoding="utf-8"))
        template = json.loads((LAB / "configs/battle_setup.json").read_text())
        cls.config, cls.settings, cls.audit = build_campaign(cls.plan, template)

    def test_roles_strength_and_identity_are_separate_from_cost(self):
        audit = self.audit
        self.assertEqual(len(audit), 42)
        self.assertEqual(len({p["identity"] for p in audit}), len(audit))
        for predicate in (lambda p: p["cost"] <= 2, lambda p: p["cost"] >= 6):
            self.assertTrue({"weak", "strong"}.issubset({p["strength"] for p in audit if predicate(p)}))
        self.assertEqual(sum(p["active"] for p in audit), 8)
        self.assertIn("未対応能力あり", design_markdown(audit))

    def test_cases_freeze_other_characters_and_do_not_optimize_speed_cost(self):
        self.assertEqual(len(self.settings["cases"]), len(self.plan["active_characters"]) * len(self.plan["encounters"]["patterns"]))
        self.assertEqual(self.settings["version"], 2)
        for case in self.settings["cases"]:
            self.assertEqual(len(case["roster"]), len(set(case["roster"])))
            self.assertEqual(case["roster"], [case["character"]])
        self.assertTrue(all(c["freeze_others"] and c["encounter"] for c in self.settings["cases"]))
        self.assertTrue(all(p["field"] in {"hp", "power"} for p in self.settings["parameters"]))
        self.assertFalse(any(p["character"] == "ammonite" and p["field"] == "power" for p in self.settings["parameters"]))
        battle = self.config["scenarios"][0]["parameters"]["battle"]
        ally = next(u for u in battle["ally_definitions"] if u["id"] == "amago")
        enemy = next(u for u in battle["enemy_baseline"] if u["id"] == "amago")
        self.assertEqual(ally["power"], 3)
        self.assertEqual(enemy["power"], 4)

    def test_duplicate_identity_is_rejected(self):
        plan = copy.deepcopy(self.plan)
        plan["profiles"][1]["identity"] = plan["profiles"][0]["identity"]
        with self.assertRaisesRegex(ValueError, "Duplicate character identity"):
            build_campaign(plan, {})

    def test_mixed_case_cannot_improve_by_changing_anchor_stats(self):
        baseline = ExperimentConfig(self.config)
        changed = apply_candidate(baseline, {"amago": {"hp": 100}, "shark": {"hp": 900}})
        case = next(c for c in self.settings["cases"] if c["id"] == "amago-3v3")
        prepared = prepare_case(baseline, changed.data["scenarios"][0], case)
        units = {d["id"]: d for d in prepared["parameters"]["battle"]["ally_definitions"]}
        self.assertEqual(units["amago"]["hp"], 100)
        self.assertEqual(units["shark"]["hp"], 60)
        untouched = changed.data["scenarios"][0]["parameters"]["battle"]["ally_definitions"]
        self.assertEqual(next(d for d in untouched if d["id"] == "shark")["hp"], 900)
