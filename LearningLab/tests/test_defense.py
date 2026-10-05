import unittest

from learning_lab.battle import BattleEnvironment, HOST_DLL
from test_battle import battle_scenario, WIDTH, HEIGHT, UNITS, BASES, HAND, SQUADS, WAIT

ALLY_UNIT_ID, ENEMY_UNIT_ID = 3, 4
MAX_STEPS = 40
POISON, BURN = 0, 2


@unittest.skipUnless(HOST_DLL.exists(), "Build BattleHost first")
class DefenseTests(unittest.TestCase):
    def setUp(self):
        self.environment = BattleEnvironment(WIDTH, HEIGHT, UNITS, BASES, HAND, SQUADS)

    def tearDown(self):
        self.environment.close()

    def scenario(self, defense):
        scenario = battle_scenario()
        battle = scenario["parameters"]["battle"]
        battle["enemy_baseline"][0].update(defense=defense, hp=2000)
        battle["ally_definitions"][0]["hp"] = 2000
        battle["initial_units"] = [
            {"team": 0, "character": "fighter", "cell": [5, 3], "destination": [10, 3]},
            {"team": 1, "character": "fighter", "cell": [6, 3], "destination": [1, 3]}]
        return scenario

    def first_damage(self, scenario, kind):
        self.environment.reset(scenario, 19)
        for _ in range(MAX_STEPS):
            transition = self.environment.step(WAIT)
            for event in transition.info["events"]:
                if event["source"] == ALLY_UNIT_ID and event["target"] == ENEMY_UNIT_ID and event["kind"] == kind:
                    return event["amount"]
        self.fail(f"Missing {kind}")

    def test_defense_reduces_only_direct_damage_and_has_minimum_one(self):
        for defense, expected in ((0, 4), (100, 2), (200, 1), (2147483647, 1)):
            with self.subTest(defense=defense):
                self.assertEqual(self.first_damage(self.scenario(defense), "direct_damage"), expected)

    def test_status_damage_ignores_defense(self):
        scenario = self.scenario(10000)
        battle = scenario["parameters"]["battle"]
        battle["ally_definitions"][0]["statuses"] = [POISON]
        battle["status_definitions"] = [{"statusId": POISON, "durationSeconds": 6, "damagePerTick": 9}]
        self.assertEqual(self.first_damage(scenario, "status_damage"), 9)

    def test_reaction_damage_ignores_defense(self):
        scenario = self.scenario(10000)
        battle = scenario["parameters"]["battle"]
        battle["ally_definitions"][0]["statuses"] = [POISON, BURN]
        battle["status_definitions"] = [
            {"statusId": POISON, "durationSeconds": 6, "damagePerTick": 1},
            {"statusId": BURN, "durationSeconds": 6, "damagePerTick": 1}]
        battle["reaction_definitions"] = [{
            "reactionId": 4, "prerequisiteStatus": POISON, "triggerStatus": BURN,
            "triggerRule": 0, "consumesPrerequisite": True, "consumesTrigger": True,
            "reactionDamage": 9, "lifetimePolicy": 0}]
        self.assertEqual(self.first_damage(scenario, "reaction_damage"), 9)

    def test_negative_defense_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.environment.reset(self.scenario(-1), 19)
