"""Bounded parameter search with frozen scripted play and explicit role targets."""
import argparse
import copy
import hashlib
import json
import math
import random
from pathlib import Path

from .artifacts import ExperimentRepository, write_json
from .config import ExperimentConfig, canonical_hash

VERSION = 2
POLICY_ID = "fixed-party-round-robin-to-default-route-v3-target-first"
ALLOWED_PARAMETERS = {"hp", "power", "speed", "cost", "defense"}
INTEGER_PARAMETERS = {"hp", "power", "cost", "defense"}
DEFENSE_CHANGE_SCALE = 100
METRICS = {"win_rate", "main_hp_ratio", "damage_per_energy", "survival_fraction"}
GLOBAL_FEATURES = 15
PARTY_FEATURES = 8
ENERGY_INDEX = 1
PARTY_PRESENT = 0
PARTY_COST = 2
PARTY_CHARACTER = 1
CHARACTER_TOLERANCE = 0.000001
ALLY = 0
DEFAULT_PARTY_SIZE = 12


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class ParameterSpace:
    def __init__(self, config: ExperimentConfig, settings: dict):
        self.axes = []
        self.baseline = {}
        for entry in settings["parameters"]:
            character, field = entry["character"], entry["field"]
            if field not in ALLOWED_PARAMETERS:
                raise ValueError(f"Unsupported parameter: {field}")
            key = (character, field)
            if key in self.axes:
                raise ValueError("Duplicate parameter axis")
            values = entry["values"]
            if not values or any(not finite(v) or v < 0 or (v == 0 and field != "defense") for v in values):
                raise ValueError("Candidates must be positive, except defense may be zero")
            if field in INTEGER_PARAMETERS and any(type(v) is not int for v in values):
                raise ValueError("HP/power/cost/defense candidates must be integers")
            baseline_values = set()
            for scenario in config.data["scenarios"]:
                definitions = scenario["parameters"]["battle"]["ally_definitions"]
                matches = [d for d in definitions if d["id"] == character]
                if len(matches) != 1:
                    raise ValueError(f"Every scenario needs one definition for {character}")
                baseline_values.add(matches[0][field])
            if len(baseline_values) != 1:
                raise ValueError("Baseline must be identical across scenarios")
            baseline = baseline_values.pop()
            if baseline not in values or len(values) != len(set(values)):
                raise ValueError("Unique candidate values must include the frozen baseline")
            self.axes.append(key)
            self.baseline.setdefault(character, {})[field] = baseline
        if not self.axes:
            raise ValueError("At least one parameter is required")
        self.values = [entry["values"] for entry in settings["parameters"]]

    def candidates(self, budget: int, seed: int):
        # Sample without replacement, including the baseline even with a budget of one.
        dimensions = [len(v) for v in self.values]
        total = math.prod(dimensions)
        baseline_index = 0
        for (character, field), values in zip(self.axes, self.values):
            baseline_index = baseline_index * len(values) + values.index(self.baseline[character][field])
        rng = random.Random(seed)
        selected = {baseline_index}
        yield copy.deepcopy(self.baseline)
        while len(selected) < min(budget, total):
            index = rng.randrange(total)
            if index in selected:
                continue
            selected.add(index)
            candidate = {}
            for (character, field), values in reversed(list(zip(self.axes, self.values))):
                index, digit = divmod(index, len(values))
                candidate.setdefault(character, {})[field] = values[digit]
            yield candidate

    def change_penalty(self, candidate):
        return sum(((candidate[c][f] - self.baseline[c][f]) /
                    (max(DEFENSE_CHANGE_SCALE, self.baseline[c][f]) if f == "defense" else self.baseline[c][f])) ** 2
                   for c, f in self.axes) / len(self.axes)


def apply_candidate(config: ExperimentConfig, candidate: dict) -> ExperimentConfig:
    data = copy.deepcopy(config.data)
    for scenario in data["scenarios"]:
        for unit in scenario["parameters"]["battle"]["ally_definitions"]:
            unit.update(candidate.get(unit["id"], {}))
    # enemy_baseline, stages, rosters, rules and policy remain untouched.
    return ExperimentConfig(data)


def prepare_case(baseline: ExperimentConfig, candidate_scenario: dict, case: dict) -> dict:
    scenario = copy.deepcopy(candidate_scenario)
    battle = scenario["parameters"]["battle"]
    if case.get("freeze_others", False):
        original = next(s for s in baseline.data["scenarios"] if s["id"] == scenario["id"])
        units = {d["id"]: d for d in original["parameters"]["battle"]["ally_definitions"]}
        battle["ally_definitions"] = [d if d["id"] == case["character"] else
            copy.deepcopy(units[d["id"]]) for d in battle["ally_definitions"]]
    battle["roster"] = copy.deepcopy(case["roster"])
    return scenario


class ScriptedSortiePolicy:
    """Deploy the target first, then visit occupied party slots in a fixed cyclic order.

    Save energy for the next slot instead of letting cheap characters starve costly ones.
    This policy has no learnable parameters and does not select cards or commit drafts.
    """
    def __init__(self, spec, party_size, required_first_character=None):
        self.wait = spec.action_names.index("wait")
        self.deployments = [spec.action_names.index(f"deploy:{i}") for i in range(party_size)]
        self.next_slot = 0
        self.required_first_character = required_first_character

    def choose(self, observation):
        occupied = [slot for slot in range(len(self.deployments))
                    if observation.values[GLOBAL_FEATURES + slot * PARTY_FEATURES + PARTY_PRESENT]]
        if not occupied:
            return self.wait
        if self.required_first_character is not None:
            preferred = [slot for slot in occupied
                         if abs(observation.values[GLOBAL_FEATURES + slot * PARTY_FEATURES + PARTY_CHARACTER]
                                - self.required_first_character) < CHARACTER_TOLERANCE]
            if not preferred:
                raise ValueError("Required target is missing from the fixed party")
            slot = preferred[0]
        else:
            slot = next((slot for slot in occupied if slot >= self.next_slot), occupied[0])
        action = self.deployments[slot]
        if not observation.action_mask[action]:
            return self.wait
        self.required_first_character = None
        self.next_slot = (slot + 1) % len(self.deployments)
        return action


class BalanceEvaluator:
    def __init__(self, config: ExperimentConfig, settings: dict):
        self.config, self.settings = config, settings
        self.validate()

    def validate(self):
        s = self.settings
        if s.get("version") != VERSION or self.config.data["environment_factory"] != "learning_lab.battle:BattleEnvironment":
            raise ValueError("Requires balance v2 and BattleEnvironment")
        if type(s["candidate_budget"]) is not int or s["candidate_budget"] <= 0:
            raise ValueError("candidate_budget must be positive")
        if type(s["search_seed"]) is not int or s["search_seed"] < 0:
            raise ValueError("search_seed must be nonnegative")
        if not finite(s["change_weight"]) or s["change_weight"] < 0:
            raise ValueError("change_weight must be nonnegative")
        if not s["cases"] or len({c["id"] for c in s["cases"]}) != len(s["cases"]):
            raise ValueError("Cases must have unique IDs")
        used_seeds = set()
        for split in ("train", "validation", "holdout"):
            seeds = s["seeds"][split]
            if not self.config.scenarios(split) or not seeds or any(type(seed) is not int or seed < 0 for seed in seeds):
                raise ValueError("All splits require scenarios and nonnegative seeds")
            if len(set(seeds)) != len(seeds) or used_seeds.intersection(seeds):
                raise ValueError("Seeds must be unique and disjoint between splits")
            used_seeds.update(seeds)
        for case in s["cases"]:
            if not case["role"] or not case["roster"] or case["character"] not in case["roster"]:
                raise ValueError("Each role case must include its target character in the roster")
            if not finite(case["weight"]) or case["weight"] <= 0 or not case["targets"]:
                raise ValueError("Each case needs positive weight and explicit targets")
            for name, target in case["targets"].items():
                if (name not in METRICS or any(not finite(target[k]) for k in ("min", "max", "scale", "weight"))
                        or target["min"] > target["max"] or target["scale"] <= 0 or target["weight"] <= 0):
                    raise ValueError("Invalid target band, scale or weight")
            for scenario in self.config.data["scenarios"]:
                battle = scenario["parameters"]["battle"]
                if battle.get("initial_units") or any(b.get("garrison") for b in battle["stage"]["bases"]):
                    raise ValueError("Balance v2 requires cases without predeployed units or garrisons")
                if (len(case["roster"]) != len(set(case["roster"])) or
                        len(case["roster"]) > battle["rules"].get("party_size", DEFAULT_PARTY_SIZE)):
                    raise ValueError("Case roster must be a unique fixed party within party_size")
                if battle.get("version") != VERSION:
                    raise ValueError("Legacy battle snapshot: rebuild the campaign for party controls")
                definitions = {d["id"]: d for d in battle["ally_definitions"]}
                if set(case["roster"]) - definitions.keys():
                    raise ValueError("Case roster contains unknown characters")
                if definitions[case["character"]]["cost"] != case["reference_cost"]:
                    raise ValueError("Case reference_cost must match the frozen character cost")

    def evaluate(self, candidate: dict, split: str, *, joint: bool = False):
        config = apply_candidate(self.config, candidate)
        environment = config.create_environment()
        results = []
        try:
            for case in self.settings["cases"]:
                case = dict(case, freeze_others=False) if joint else case
                episodes = []
                for original in config.scenarios(split):
                    scenario = prepare_case(self.config, original, case)
                    battle = scenario["parameters"]["battle"]
                    for seed in self.settings["seeds"][split]:
                        obs = environment.reset(scenario, seed)
                        required_first = None
                        if case.get("target_first", False):
                            ids = [d["id"] for d in battle["ally_definitions"]]
                            required_first = (ids.index(case["character"]) + 1) / len(ids)
                        policy = ScriptedSortiePolicy(environment.spec,
                            battle["rules"].get("party_size", DEFAULT_PARTY_SIZE), required_first)
                        for _ in range(config.data["evaluation"]["max_decisions"]):
                            transition = environment.step(policy.choose(obs))
                            obs = transition.observation
                            if transition.ended:
                                break
                        else:
                            raise RuntimeError("Balance evaluation watchdog exceeded")
                        state = environment.save_state()["debug"]
                        main_index = next(i for i, b in enumerate(battle["stage"]["bases"]) if b["main"])
                        main_hp = max(0, state["bases"][main_index]["hp"]) / battle["stage"]["bases"][main_index]["hp"]
                        metric = next((m for m in transition.info["metrics"] if m["team"] == ALLY and
                                       m["character"] == case["character"]), None)
                        deployed = metric["deployed"] if metric else 0
                        cost = next(d["cost"] for d in battle["ally_definitions"] if d["id"] == case["character"])
                        if not deployed:
                            raise ValueError(f"No exposure for {case['id']}; revise roster, budget or policy")
                        episodes.append({"scenario": scenario["id"], "seed": seed,
                            "weight": scenario["weight"], "outcome": transition.info["outcome"],
                            "deployed": deployed, "metrics": {
                                "win_rate": float(transition.info["outcome"] == "victory"),
                                "main_hp_ratio": main_hp,
                                "damage_per_energy": metric["damage"] / (deployed * cost),
                                "survival_fraction": (deployed - metric["deaths"]) / deployed}})
                total_weight = sum(e["weight"] for e in episodes)
                means = {name: sum(e["metrics"][name] * e["weight"] for e in episodes) / total_weight for name in METRICS}
                loss = sum(t["weight"] * (max(t["min"] - means[name], means[name] - t["max"], 0) / t["scale"]) ** 2
                           for name, t in case["targets"].items()) / sum(t["weight"] for t in case["targets"].values())
                results.append({"case": case["id"], "role": case["role"], "reference_cost": case["reference_cost"],
                                "loss": loss, "weight": case["weight"], "means": means, "episodes": episodes})
        finally:
            environment.close()
        return {"split": split, "loss": sum(c["loss"] * c["weight"] for c in results) / sum(c["weight"] for c in results),
                "cases": results}


class BalanceSearch:
    def __init__(self, config, settings):
        self.config, self.settings = config, settings
        self.evaluator = BalanceEvaluator(config, settings)
        self.space = ParameterSpace(config, settings)
        covered = {c["character"] for c in settings["cases"]}
        if any(c not in covered for c, _ in self.space.axes):
            raise ValueError("Every adjusted character needs an evaluation case")

    def run(self, directory: Path):
        directory = directory.resolve()
        assets = Path(__file__).resolve().parents[2] / "Assets"
        if directory.is_relative_to(assets.resolve()):
            raise ValueError("Balance artifacts must stay outside Assets")
        repository = ExperimentRepository(directory)
        repository.initialize({"version": VERSION, "config": self.config.data, "settings": self.settings,
                               "config_hash": self.config.digest, "policy": POLICY_ID,
                               "search_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        trials = []
        for index, candidate in enumerate(self.space.candidates(self.settings["candidate_budget"], self.settings["search_seed"])):
            evaluation = self.evaluator.evaluate(candidate, "train")
            score = evaluation["loss"] + self.settings["change_weight"] * self.space.change_penalty(candidate)
            trial = {"index": index, "candidate": candidate, "candidate_hash": canonical_hash(candidate),
                     "score": score, "evaluation": evaluation}
            trials.append(trial)
            write_json(directory / f"trial-{index:04d}.json", trial)
            print(json.dumps({"trial": index, "score": score}), flush=True)
        best = min(trials, key=lambda t: (t["score"], t["index"]))
        baseline_validation = self.evaluator.evaluate(self.space.baseline, "validation")
        selected_validation = (baseline_validation if best["index"] == 0 else
                               self.evaluator.evaluate(best["candidate"], "validation"))
        baseline_case_losses = {c["case"]: c["loss"] for c in baseline_validation["cases"]}
        cases_preserved = all(c["loss"] <= baseline_case_losses[c["case"]]
                              for c in selected_validation["cases"])
        accepted = (best["index"] != 0 and selected_validation["loss"] <= baseline_validation["loss"]
                    and cases_preserved)
        passed_individual_validation = accepted
        requires_joint = any(case.get("freeze_others", False) for case in self.settings["cases"])
        joint_validation = None
        if accepted and requires_joint:
            joint_validation = self.evaluator.evaluate(best["candidate"], "validation", joint=True)
            # Baseline values are identical in isolated and joint evaluation.
            accepted = joint_validation["loss"] <= baseline_validation["loss"] and all(
                c["loss"] <= baseline_case_losses[c["case"]] for c in joint_validation["cases"])
        recommended = best["candidate"] if accepted else self.space.baseline
        # Holdout is reported once, after selection; never used to select a different candidate.
        holdout = (self.evaluator.evaluate(recommended, "holdout", joint=True) if requires_joint else
                   self.evaluator.evaluate(recommended, "holdout"))
        report = {"version": VERSION, "policy": POLICY_ID, "config_hash": self.config.digest,
                  "candidate_count": len(trials), "selected_train_candidate": best["candidate"],
                  "baseline_train_score": trials[0]["score"], "selected_train_score": best["score"],
                  "passed_validation_gate": accepted, "recommended_overrides": recommended,
                  "passed_individual_validation": passed_individual_validation,
                  "joint_validation": joint_validation, "holdout_joint": requires_joint,
                  "baseline_validation": baseline_validation, "selected_validation": selected_validation,
                  "holdout": holdout, "applied_to_game": False}
        write_json(directory / "report.json", report)
        write_json(directory / "recommended-config.json", apply_candidate(self.config, recommended).data)
        return report


def main():
    parser = argparse.ArgumentParser(description="Tune character parameters with fixed scripted play")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.settings.read_text(encoding="utf-8-sig"))
    report = BalanceSearch(ExperimentConfig.load(args.config), settings).run(args.run)
    print(json.dumps({"passed_validation_gate": report["passed_validation_gate"],
                      "recommended_overrides": report["recommended_overrides"]}))


if __name__ == "__main__":
    main()
