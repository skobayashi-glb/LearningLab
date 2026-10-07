import copy
import json
from pathlib import Path
import unittest

from learning_lab.battle import HOST_DLL
from learning_lab.balance import ScriptedSortiePolicy
from learning_lab.config import ExperimentConfig
from learning_lab.export_battle import BattleExporter, enum_array

LAB = Path(__file__).resolve().parents[1]
MAX_DECISIONS = 600


@unittest.skipUnless(HOST_DLL.exists(), "Build BattleHost first")
class BattleExportTests(unittest.TestCase):
    def test_editor_maps_real_characters_and_frozen_enemy_snapshot(self):
        setup = json.loads((LAB / "configs/battle_setup.json").read_text())
        for scenario in setup["scenarios"]:
            scenario["ally_overrides"] = {"shark": {"hp": 120}}
        config = ExperimentConfig(BattleExporter().export(setup))
        battle = config.data["scenarios"][0]["parameters"]["battle"]
        ally = next(d for d in battle["ally_definitions"] if d["id"] == "shark")
        enemy = next(d for d in battle["enemy_baseline"] if d["id"] == "shark")
        self.assertEqual(ally["hp"], 120)
        self.assertNotEqual(enemy["hp"], ally["hp"])
        self.assertGreater(ally["impact_seconds"], 0)
        environment = config.create_environment()
        try:
            for scenario in config.data["scenarios"]:
                observation = environment.reset(scenario, 2001)
                policy = ScriptedSortiePolicy(environment.spec, setup["envelope"]["party"])
                for _ in range(MAX_DECISIONS):
                    action = policy.choose(observation)
                    transition = environment.step(action)
                    observation = transition.observation
                    if transition.ended:
                        break
                self.assertTrue(transition.ended)
                self.assertIn(transition.info["outcome"], {"victory", "defeat", "timeout"})
                self.assertTrue(any(m["damage"] > 0 for m in transition.info["metrics"]))
        finally:
            environment.close()

    def test_enum_hex_preserves_zero_values(self):
        self.assertEqual(enum_array("000000000200000005000000"), [0, 2, 5])

    def test_unsupported_character_is_rejected(self):
        exporter = BattleExporter()
        path, original = exporter.characters["shark"]
        unsupported = copy.deepcopy(original)
        unsupported["moveType"] = 1
        exporter.characters["shark"] = (path, unsupported)
        with self.assertRaisesRegex(ValueError, "movement"):
            exporter.character("shark")
