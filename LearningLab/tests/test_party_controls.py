import copy
import unittest

from learning_lab.battle import BattleEnvironment, HOST_DLL
from learning_lab.config import canonical_hash
from test_battle import battle_scenario, WIDTH, HEIGHT, UNITS, BASES, PARTY, ROUTES, WAYPOINTS

FIRST_SLOT = 0
ENERGY_AFTER_FIRST_SORTIE = 18
ENERGY_AFTER_ONE_SECOND = 19
REPEATED_SORTIES = 6
BASE_HP = 10000
CELL_SIZE = 3.2
PATH_STEPS = 32


@unittest.skipUnless(HOST_DLL.exists(), "Build BattleHost first")
class PartyControlTests(unittest.TestCase):
    def setUp(self):
        self.environment = BattleEnvironment(WIDTH, HEIGHT, UNITS, BASES, PARTY, ROUTES, WAYPOINTS)
        self.scenario = battle_scenario()
        for base in self.scenario["parameters"]["battle"]["stage"]["bases"]:
            base["hp"] = BASE_HP

    def tearDown(self):
        self.environment.close()

    def reset(self):
        return self.environment.reset(self.scenario, 31)

    def step(self, name):
        return self.environment.step(self.environment.spec.action_names.index(name))

    def state(self):
        return self.environment.save_state()["debug"]

    def test_same_slot_repeated_immediate_sorties_without_five_squad_limit(self):
        self.reset()
        for _ in range(REPEATED_SORTIES):
            transition = self.step("deploy:0")
            self.assertTrue(transition.observation.action_mask[self.environment.spec.action_names.index("deploy:0")])
        state = self.state()
        self.assertEqual(len(state["units"]), REPEATED_SORTIES)
        self.assertEqual(len({u["squad"] for u in state["units"]}), REPEATED_SORTIES)
        self.assertEqual(state["party"], ["fighter"])
        self.assertTrue(all(u["route"] == FIRST_SLOT for u in state["units"]))
        self.assertFalse(any(n.startswith(("card:", "squad:")) or n in {"commit", "discard", "clear"}
                             for n in self.environment.spec.action_names))

    def test_one_second_energy_recovery(self):
        self.reset()
        self.step("deploy:0")
        self.assertEqual(self.state()["energy"][0], ENERGY_AFTER_FIRST_SORTIE)
        self.step("wait")
        self.assertEqual(self.state()["energy"][0], ENERGY_AFTER_ONE_SECOND)

    def test_route_selection_advances_time_without_retargeting_previous_units(self):
        self.reset()
        self.step("deploy:0")
        before = self.state()
        transition = self.step("route:1")
        after = self.state()
        self.assertGreater(after["tick"], before["tick"])
        self.assertEqual(transition.info["discount_steps"], 1)
        self.assertEqual(after["units"][0]["route"], FIRST_SLOT)
        self.assertEqual(after["units"][0]["destination"], before["units"][0]["destination"])
        self.assertFalse(transition.observation.action_mask[self.environment.spec.action_names.index("deploy:0")])
        self.step("cell:4:5")
        self.step("deploy:0")
        self.assertEqual([u["route"] for u in self.state()["units"]], [0, 1])

    def test_waypoint_cap_restart_and_cancel(self):
        self.reset()
        self.step("route:0")
        for name in ("cell:4:5", "cell:7:5", "cell:8:4", "cell:9:3"):
            self.step(name)
        route = self.state()["routes"][FIRST_SLOT]
        self.assertEqual(route["waypoints"], [5 * WIDTH + 4, 5 * WIDTH + 7])
        self.assertEqual(route["destination"], 3 * WIDTH + 9)
        self.step("route:0")
        self.step("cancel-route-edit")
        self.step("cell:8:2")
        self.assertEqual(self.state()["routes"][FIRST_SLOT]["waypoints"], route["waypoints"])
        self.step("route:0")
        self.step("cell:3:1")
        self.assertEqual(self.state()["routes"][FIRST_SLOT]["waypoints"], [])

    def test_route_edit_updates_only_bound_units_and_replay_preserves_paths(self):
        self.reset()
        self.step("deploy:0")
        self.step("route:1")
        self.step("cell:4:5")
        self.step("deploy:0")
        old = self.state()["units"][0]["destination"]
        self.step("route:1")
        self.step("cell:8:5")
        state = self.state()
        self.assertEqual(state["units"][0]["destination"], old)
        self.assertEqual(state["units"][1]["destination"], 5 * WIDTH + 8)
        snapshot = self.environment.save_state()
        self.step("wait")
        expected = self.environment.save_state()
        self.environment.load_state(snapshot)
        self.step("wait")
        self.assertEqual(canonical_hash(expected), canonical_hash(self.environment.save_state()))

    def test_base_change_affects_only_future_sorties(self):
        bases = self.scenario["parameters"]["battle"]["stage"]["bases"]
        extra = copy.deepcopy(bases[0])
        extra.update(id="outpost", main=False, cell=[1, 5], footprint=[[1, 5]], spawn_cells=[[2, 5]])
        bases.append(extra)
        self.reset()
        self.step("deploy:0")
        before = self.state()["units"][0]["path"]
        self.step("base:2")
        self.step("deploy:0")
        self.assertEqual(self.state()["units"][0]["path"], before)
        self.assertEqual(self.state()["units"][1]["path"][0], 5 * WIDTH + 2)

    def test_delete_route_rebinds_and_last_delete_retains_destination(self):
        self.reset()
        self.step("deploy:0")
        self.step("route:1")
        self.step("cell:4:5")
        self.step("route-remove:0")
        state = self.state()
        self.assertEqual(state["units"][0]["route"], 1)
        self.assertEqual(state["units"][0]["destination"], 5 * WIDTH + 4)
        path = state["units"][0]["path"]
        self.step("route-remove:1")
        state = self.state()
        self.assertEqual(state["selected_route"], -1)
        self.assertEqual(state["units"][0]["route"], -1)
        self.assertEqual(state["units"][0]["path"], path)
        self.assertEqual(state["units"][0]["destination"], 5 * WIDTH + 4)
        self.step("route:2")
        self.step("cell:4:1")
        self.step("deploy:0")
        self.assertEqual(self.state()["units"][-1]["route"], 2)

    def test_units_visit_waypoint_instead_of_cutting_to_destination(self):
        self.reset()
        self.step("route:0")
        self.step("cell:2:6")
        self.step("cell:6:6")
        self.step("deploy:0")
        waypoint_visited = False
        for _ in range(PATH_STEPS):
            unit = self.state()["units"][0]
            waypoint_visited |= abs(unit["x"] / CELL_SIZE - 2) < 0.1 and abs(unit["z"] / CELL_SIZE - 6) < 0.1
            self.step("wait")
        self.assertTrue(waypoint_visited)
        unit = self.state()["units"][0]
        self.assertAlmostEqual(unit["x"] / CELL_SIZE, 6, places=2)
        self.assertAlmostEqual(unit["z"] / CELL_SIZE, 6, places=2)

    def test_empty_slot_and_unaffordable_sortie_are_masked(self):
        self.scenario["parameters"]["battle"]["rules"]["initial_energy"] = 0
        observation = self.reset()
        self.assertFalse(observation.action_mask[self.environment.spec.action_names.index("deploy:0")])
        self.assertFalse(observation.action_mask[self.environment.spec.action_names.index("deploy:1")])

    def test_legacy_snapshot_is_rejected(self):
        self.scenario["parameters"]["battle"]["version"] = 1
        with self.assertRaisesRegex(RuntimeError, "Invalid battle rules"):
            self.reset()

    def test_legacy_repeated_card_roster_is_rejected(self):
        self.scenario["parameters"]["battle"]["roster"] = ["fighter", "fighter"]
        with self.assertRaisesRegex(RuntimeError, "unique characters"):
            self.reset()

    def test_observation_capacity_overflow_fails_instead_of_capping_sorties(self):
        self.environment.close()
        self.environment = BattleEnvironment(WIDTH, HEIGHT, 1, BASES, PARTY, ROUTES, WAYPOINTS)
        self.reset()
        first = self.step("deploy:0")
        self.assertTrue(first.observation.action_mask[self.environment.spec.action_names.index("deploy:0")])
        with self.assertRaisesRegex(RuntimeError, "enlarge observation envelope"):
            self.step("deploy:0")


if __name__ == "__main__":
    unittest.main()
