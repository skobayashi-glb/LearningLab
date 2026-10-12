import copy
import unittest

import numpy as np
import torch

from learning_lab.battle import BattleEnvironment, HOST_DLL
from learning_lab.config import canonical_hash
from learning_lab.ppo import generalized_advantages

WIDTH, HEIGHT, UNITS, BASES, PARTY, ROUTES, WAYPOINTS = 12, 7, 32, 6, 12, 3, 2
WAIT, FIRST_DEPLOY = 0, 2
MAX_DECISIONS = 240


def character(id="fighter", hp=20, power=4, cost=2, statuses=None):
    return {
        "id": id, "hp": hp, "power": power, "cost": cost, "speed": 2,
        "impact_seconds": 0.1, "recovery_seconds": 0.2, "range": 3.2,
        "lunge": 0, "ranged": False, "ignore_sight": False, "move_type": 0,
        "target_selection": 0, "max_targets": 1,
        "body": {"size": {"x": 2, "y": 2, "z": 2}, "offset": {"x": 0, "y": 0, "z": 0}},
        "shape": {"type": 1}, "engagement": {"circle_radius": 6, "sector_radius": 6, "sector_angle": 360},
        "statuses": statuses or [], "knockback_hp_ratios": []}


def battle_scenario():
    ally, enemy = character(), character()
    return {"id": "test-battle", "family": "test", "split": "train", "weight": 1,
            "parameters": {"battle": {
                "version": 2, "cell_size": 3.2,
                "stage": {"id": "test-map", "width": WIDTH, "height": HEIGHT,
                          "terrain": [0] * (WIDTH * HEIGHT), "generation_seed": 101,
                          "wall_candidate_path_rate": 0,
                          "bases": [
                              {"id": "main", "team": 0, "main": True, "boss": False, "hp": 20,
                               "cell": [1, 3], "footprint": [[1, 3]], "spawn_cells": [[2, 3]], "garrison": []},
                              {"id": "boss", "team": 1, "main": False, "boss": True, "hp": 20,
                               "cell": [10, 3], "footprint": [[10, 3]], "spawn_cells": [[9, 3]], "garrison": []}]},
                "rules": {"initial_energy": 20, "enemy_initial_energy": 20, "max_energy": 20,
                          "enemy_enabled": False, "episode_seconds": 60},
                "roster": ["fighter"], "enemy_pool": ["fighter"],
                "ally_definitions": [ally], "enemy_baseline": [enemy]}}}


@unittest.skipUnless(HOST_DLL.exists(), "Build BattleHost first")
class BattleTests(unittest.TestCase):
    def setUp(self):
        self.environment = BattleEnvironment(WIDTH, HEIGHT, UNITS, BASES, PARTY, ROUTES, WAYPOINTS)

    def tearDown(self):
        self.environment.close()

    def test_single_click_deploy_advances_combat_and_captures_boss(self):
        observation = self.environment.reset(battle_scenario(), 31)
        self.assertTrue(observation.action_mask[FIRST_DEPLOY])
        transition = self.environment.step(FIRST_DEPLOY)
        self.assertGreater(self.environment.save_state()["debug"]["tick"], 0)
        self.assertEqual(transition.info["discount_steps"], 1)
        self.assertEqual(self.environment.save_state()["debug"]["energy"][0], 18)
        for _ in range(MAX_DECISIONS):
            if transition.ended:
                break
            transition = self.environment.step(WAIT)
        self.assertEqual(transition.info["outcome"], "victory")
        self.assertTrue(transition.terminated)
        self.assertGreater(transition.reward, 1)
        self.assertTrue(any(row["damage"] >= 20 for row in transition.info["metrics"]))

    def test_enemy_budgeted_random_sortie_and_main_base_defeat(self):
        scenario = battle_scenario()
        rules = scenario["parameters"]["battle"]["rules"]
        rules.update({"enemy_enabled": True, "enemy_decision_seconds": 0.5,
                      "enemy_forecast_seconds": 0.5, "enemy_duplicate_limit": 1})
        self.environment.reset(scenario, 42)
        enemy_spawned = False
        for _ in range(MAX_DECISIONS):
            transition = self.environment.step(WAIT)
            state = self.environment.save_state()["debug"]
            enemy_spawned |= any(u["team"] == 1 for u in state["units"])
            self.assertGreaterEqual(state["energy"][1], 0)
            self.assertLessEqual(sum(u["team"] == 1 for u in state["units"]), 1)
            if transition.ended:
                break
        self.assertTrue(enemy_spawned)
        self.assertEqual(transition.info["outcome"], "defeat")
        self.assertEqual(transition.reward, -1)

    def test_wall_blocks_route_instead_of_teleporting(self):
        scenario = battle_scenario()
        terrain = scenario["parameters"]["battle"]["stage"]["terrain"]
        for y in range(HEIGHT):
            terrain[y * WIDTH + WIDTH // 2] = 2
        observation = self.environment.reset(scenario, 31)
        self.assertFalse(observation.action_mask[FIRST_DEPLOY])

    def test_death_preserves_fixed_party_without_refunding_energy(self):
        scenario = battle_scenario()
        data = scenario["parameters"]["battle"]
        data["ally_definitions"][0]["hp"] = 1
        data["enemy_baseline"][0]["hp"] = 100
        data["initial_units"] = [
            {"team": 0, "character": "fighter", "cell": [5, 3], "destination": [10, 3]},
            {"team": 1, "character": "fighter", "cell": [6, 3], "destination": [1, 3]}]
        self.environment.reset(scenario, 31)
        for _ in range(MAX_DECISIONS):
            transition = self.environment.step(WAIT)
            state = self.environment.save_state()["debug"]
            if any(m["team"] == 0 and m["deaths"] for m in state["metrics"]):
                break
        self.assertEqual(state["party"], ["fighter"])
        self.assertEqual(state["energy"][0], 20)

    def test_replay_restores_pending_attacks_and_enemy_random_state(self):
        scenario = battle_scenario()
        scenario["parameters"]["battle"]["rules"]["enemy_enabled"] = True
        for base in scenario["parameters"]["battle"]["stage"]["bases"]:
            base["hp"] = 1000
        self.environment.reset(scenario, 12)
        self.environment.step(FIRST_DEPLOY)
        snapshot = self.environment.save_state()
        for _ in range(10):
            self.environment.step(WAIT)
        expected = self.environment.save_state()
        self.environment.load_state(snapshot)
        for _ in range(10):
            self.environment.step(WAIT)
        self.assertEqual(canonical_hash(expected), canonical_hash(self.environment.save_state()))

    def test_enemy_baseline_is_not_changed_by_ally_override(self):
        scenario = battle_scenario()
        scenario["parameters"]["battle"]["ally_definitions"][0]["hp"] = 900
        self.environment.reset(scenario, 12)
        saved = self.environment.save_state()
        data = saved["scenario"]["parameters"]["battle"]
        self.assertEqual(data["enemy_baseline"][0]["hp"], 20)
        self.assertEqual(data["ally_definitions"][0]["hp"], 900)

    def test_shared_status_reaction_causes_damage_and_consumes_materials(self):
        scenario = battle_scenario()
        data = scenario["parameters"]["battle"]
        data["ally_definitions"] = [character(statuses=[0, 2])]
        data["enemy_baseline"][0]["hp"] = 200
        data["status_definitions"] = [
            {"statusId": 0, "durationSeconds": 6, "damagePerTick": 1},
            {"statusId": 2, "durationSeconds": 6, "damagePerTick": 1}]
        data["reaction_definitions"] = [{
            "reactionId": 4, "prerequisiteStatus": 0, "triggerStatus": 2,
            "triggerRule": 0, "consumesPrerequisite": True, "consumesTrigger": True,
            "reactionDamage": 9, "lifetimePolicy": 0}]
        data["initial_units"] = [
            {"team": 0, "character": "fighter", "cell": [5, 3], "destination": [10, 3]},
            {"team": 1, "character": "fighter", "cell": [6, 3], "destination": [1, 3]}]
        self.environment.reset(scenario, 5)
        events = []
        for _ in range(20):
            transition = self.environment.step(WAIT)
            events.extend(transition.info["events"])
            if any(e["kind"] == "reaction_damage" for e in events):
                break
        self.assertTrue(any(e["kind"] == "reaction_damage" and e["amount"] == 9 for e in events))
        enemies = [u for u in self.environment.save_state()["debug"]["units"] if u["team"] == 1]
        self.assertTrue(all(not u["statuses"] for u in enemies))

    def test_generic_gae_supports_zero_duration_contract(self):
        advantages, returns = generalized_advantages(
            torch.tensor([0.0, 1.0]), torch.zeros(2), torch.zeros(2),
            torch.tensor([False, True]), torch.tensor([False, False]), 0.5, 0.5,
            torch.tensor([0.0, 1.0]))
        np.testing.assert_allclose(returns.numpy(), [1, 1])

    def test_height_offset_does_not_prevent_horizontal_combat(self):
        scenario = battle_scenario()
        for definitions in ("ally_definitions", "enemy_baseline"):
            scenario["parameters"]["battle"][definitions][0]["body"]["offset"]["y"] = 30
        self.environment.reset(scenario, 31)
        self.environment.step(FIRST_DEPLOY)
        for _ in range(MAX_DECISIONS):
            transition = self.environment.step(WAIT)
            if transition.terminated or transition.truncated:
                break
        self.assertEqual(transition.info["outcome"], "victory")

    def test_unknown_rule_is_rejected(self):
        scenario = battle_scenario()
        scenario["parameters"]["battle"]["rules"]["misspelled_energy_interval"] = 1
        with self.assertRaises(RuntimeError):
            self.environment.reset(scenario, 31)


if __name__ == "__main__":
    unittest.main()
