import copy
import json
from pathlib import Path
import unittest

from learning_lab.balance import BalanceEvaluator, RangeParameterSpace
from learning_lab.battle import BattleEnvironment, HOST_DLL
from learning_lab.campaign import build_campaign
from learning_lab.config import ExperimentConfig, canonical_hash
from learning_lab.encounters import SINGLE_WAVE, SingleWaveEncounter
from test_battle import battle_scenario, WIDTH, HEIGHT, UNITS, BASES, PARTY, ROUTES, WAYPOINTS, WAIT, FIRST_DEPLOY, MAX_DECISIONS

LAB = Path(__file__).resolve().parents[1]
SEED = 713
MULTIPLE = 3
ALLY, ENEMY = 0, 1
ONE = 1
EMPTY_ENERGY = 0


def encounter_case():
    return {"id": "fighter-3v3", "character": "fighter", "encounter": {
        "ally_count": MULTIPLE, "enemy_count": MULTIPLE, "ally_pool": ["fighter"], "enemy_pool": ["fighter"]}}


class EncounterSamplerTests(unittest.TestCase):
    def test_exact_counts_target_exposure_and_candidate_independent_sampling(self):
        case = encounter_case()
        sampler = SingleWaveEncounter(case)
        baseline = battle_scenario()
        candidate = copy.deepcopy(baseline)
        candidate["parameters"]["battle"]["ally_definitions"][0]["hp"] *= MULTIPLE
        first, matchup = sampler.prepare(baseline, SEED)
        changed, repeated = sampler.prepare(candidate, SEED)
        self.assertEqual(matchup, repeated)
        self.assertEqual(matchup["ally"].count(case["character"]), MULTIPLE)
        self.assertEqual(len(matchup["enemy"]), MULTIPLE)
        self.assertEqual(first["parameters"]["battle"]["roster"], [case["character"]])
        self.assertFalse(first["parameters"]["battle"]["rules"]["enemy_enabled"])
        self.assertNotIn("opening_wave", baseline["parameters"]["battle"])

    def test_invalid_count_empty_pool_and_missing_target_are_rejected(self):
        for side, key, value in (("ally", "count", EMPTY_ENERGY), ("enemy", "pool", []),
                                 ("ally", "pool", ["unknown"]), ("enemy", "pool", "fighter")):
            case = encounter_case()
            case["encounter"][f"{side}_{key}"] = value
            with self.assertRaises(ValueError):
                SingleWaveEncounter(case)


@unittest.skipUnless(HOST_DLL.exists(), "Build BattleHost first")
class SingleWaveHostTests(unittest.TestCase):
    def setUp(self):
        self.environment = BattleEnvironment(WIDTH, HEIGHT, UNITS, BASES, PARTY, ROUTES, WAYPOINTS)

    def tearDown(self):
        self.environment.close()

    def scenario(self):
        scenario, _ = SingleWaveEncounter(encounter_case()).prepare(battle_scenario(), SEED)
        rules = scenario["parameters"]["battle"]["rules"]
        rules.update(initial_energy=EMPTY_ENERGY, enemy_initial_energy=EMPTY_ENERGY)
        return scenario

    def test_both_armies_registered_at_time_zero_without_energy_gate(self):
        obs = self.environment.reset(self.scenario(), SEED)
        state = self.environment.save_state()["debug"]
        self.assertEqual(state["tick"], EMPTY_ENERGY)
        self.assertEqual(state["energy"], [EMPTY_ENERGY, EMPTY_ENERGY])
        for team in (ALLY, ENEMY):
            self.assertEqual(sum(u["team"] == team for u in state["units"]), MULTIPLE)
        self.assertFalse(obs.action_mask[FIRST_DEPLOY])
        self.assertTrue(all(not u["spawned"] for u in state["units"]))

    def test_each_side_uses_the_spawn_port_and_never_reinforces(self):
        self.environment.reset(self.scenario(), SEED)
        transition = self.environment.step(WAIT)
        spawn_events = [event for event in transition.info["events"] if event["kind"] == "spawn"]
        self.assertTrue(spawn_events)
        for _ in range(MAX_DECISIONS):
            state = self.environment.save_state()["debug"]
            for team in (ALLY, ENEMY):
                deployed = sum(m["deployed"] for m in state["metrics"] if m["team"] == team)
                self.assertEqual(deployed, MULTIPLE)
            self.assertFalse(any(e["kind"] in ("deploy", "enemy_forecast") for e in transition.info["events"]))
            if transition.ended:
                break
            transition = self.environment.step(WAIT)
        self.assertTrue(transition.ended)

    def test_wave_replay_restores_queue_and_battle_state(self):
        self.environment.reset(self.scenario(), SEED)
        self.environment.step(WAIT)
        checkpoint = self.environment.save_state()
        self.environment.step(WAIT)
        expected = canonical_hash(self.environment.save_state())
        self.environment.load_state(checkpoint)
        self.environment.step(WAIT)
        self.assertEqual(canonical_hash(self.environment.save_state()), expected)

    def test_enemy_ai_cannot_be_enabled_for_an_opening_wave(self):
        scenario = self.scenario()
        scenario["parameters"]["battle"]["rules"]["enemy_enabled"] = True
        with self.assertRaises(RuntimeError):
            self.environment.reset(scenario, SEED)


@unittest.skipUnless(HOST_DLL.exists(), "Build BattleHost first")
class SingleWaveCampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((LAB / "configs/defense_training_plan.json").read_text(encoding="utf-8"))
        setup = json.loads((LAB / "configs/defense_training_stages.json").read_text(encoding="utf-8"))
        data, cls.settings, cls.audit = build_campaign(cls.plan, setup)
        cls.config = ExperimentConfig(data)

    def test_current_campaign_covers_four_patterns_and_reports_unsupported_characters(self):
        supported = {entry["id"] for entry in self.audit if entry["supported"]}
        sampling = self.settings["character_sampling"]
        self.assertEqual(self.settings["evaluation_mode"], SINGLE_WAVE)
        self.assertEqual(set(sampling["ally_pool"]), supported)
        self.assertEqual(set(sampling["enemy_pool"]), supported)
        self.assertEqual(len(supported) + len(sampling["unsupported"]), len(self.audit))
        self.assertTrue(all(entry["reason"] for entry in sampling["unsupported"]))
        self.assertEqual({(case["encounter"]["ally_count"], case["encounter"]["enemy_count"])
                          for case in self.settings["cases"]}, {(ONE, ONE), (ONE, MULTIPLE), (MULTIPLE, ONE), (MULTIPLE, MULTIPLE)})

    def test_real_two_map_evaluation_logs_exact_armies_with_frozen_enemy_data(self):
        settings = copy.deepcopy(self.settings)
        settings["cases"] = [case for case in settings["cases"] if case["character"] == "amago"]
        settings["seeds"] = {split: seeds[:ONE] for split, seeds in settings["seeds"].items()}
        evaluator = BalanceEvaluator(self.config, settings)
        baseline = RangeParameterSpace(self.config, self.settings).baseline
        before = self.config.digest
        result = evaluator.evaluate(baseline, "train")
        self.assertEqual(self.config.digest, before)
        for case in result["cases"]:
            definition = next(entry for entry in settings["cases"] if entry["id"] == case["case"])
            counts = definition["encounter"]
            for episode in case["episodes"]:
                wave = episode["encounter"]
                self.assertEqual(len(wave["ally"]), counts["ally_count"])
                self.assertEqual(len(wave["enemy"]), counts["enemy_count"])
                self.assertEqual(episode["deployed"], wave["ally"].count(definition["character"]))
            # Both geometries use the same sampled armies for the same case/seed.
            self.assertEqual(case["episodes"][0]["encounter"], case["episodes"][-ONE]["encounter"])


if __name__ == "__main__":
    unittest.main()
