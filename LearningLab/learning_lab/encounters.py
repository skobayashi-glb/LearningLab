"""Candidate-independent, reproducible single-wave combat matchups."""
import copy
import random

from .config import canonical_hash

SINGLE_WAVE = "single-wave"
CYCLIC_SORTIES = "cyclic-sorties"
MINIMUM_ARMY_SIZE = 1
ENCOUNTER_HASH_BASE = 16


class SingleWaveEncounter:
    def __init__(self, case: dict):
        self.case = case
        self.settings = case["encounter"]
        for side in ("ally", "enemy"):
            count = self.settings[f"{side}_count"]
            pool = self.settings[f"{side}_pool"]
            if type(count) is not int or count < MINIMUM_ARMY_SIZE:
                raise ValueError("Single-wave army counts must be positive integers")
            if not isinstance(pool, list) or not pool or any(not isinstance(id, str) or not id for id in pool) or len(pool) != len(set(pool)):
                raise ValueError("Single-wave character pools must be nonempty and unique")
        if case["character"] not in self.settings["ally_pool"]:
            raise ValueError("The evaluated character must be in the ally pool")

    def prepare(self, scenario: dict, seed: int) -> tuple[dict, dict]:
        digest = canonical_hash({"case": self.case["id"], "seed": seed})
        rng = random.Random(int(digest, ENCOUNTER_HASH_BASE))
        # Every target is exposed; companions and opponents are sampled with replacement.
        allies = [self.case["character"]] + rng.choices(
            self.settings["ally_pool"], k=self.settings["ally_count"] - MINIMUM_ARMY_SIZE)
        enemies = rng.choices(self.settings["enemy_pool"], k=self.settings["enemy_count"])
        result = copy.deepcopy(scenario)
        battle = result["parameters"]["battle"]
        battle["roster"] = list(dict.fromkeys(allies))
        battle["rules"]["enemy_enabled"] = False
        battle["opening_wave"] = {"ally": allies, "enemy": enemies}
        return result, {"seed": seed, "ally": allies, "enemy": enemies}
