"""Bounded parameter search with frozen scripted play and explicit role targets."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from .artifacts import CHECKPOINT_VERSION, ExperimentRepository, write_json
from .config import DISJOINT_FAMILIES, ExperimentConfig, canonical_hash
from .encounters import CYCLIC_SORTIES, SINGLE_WAVE, SingleWaveEncounter
from .parameter_space import ParameterSpace, RangeParameterSpace, finite
from .adaptive_search import AdaptiveProposalLearner, ADAPTIVE_STRATEGY, RANDOM_STRATEGY
from .monitoring import SearchObserver
from .cost_balance import BUDGET_WAVE, BudgetWaveEncounter, battle_score, LEGS
from .character_search import PER_CHARACTER, CharacterProposalLearner
from .validation import preserved_validation, ValidationPolicy, AGGREGATE
from .search_continuation import SearchContinuation, TRIAL_INCREMENT

VERSION = 2
POLICY_ID = "fixed-party-round-robin-to-default-route-v3-target-first"
SINGLE_WAVE_POLICY_ID = "one-opening-wave-per-team-then-wait-v1"
METRICS = {"win_rate", "main_hp_ratio", "damage_per_energy", "survival_fraction", "match_score", "enemy_hp_ratio", "team_survival_fraction"}
GLOBAL_FEATURES = 15
PARTY_FEATURES = 8
ENERGY_INDEX = 1
PARTY_PRESENT = 0
PARTY_COST = 2
PARTY_CHARACTER = 1
CHARACTER_TOLERANCE = 0.000001
ALLY = 0
DEFAULT_PARTY_SIZE = 12
DEFAULT_UNIT_CAPACITY = 256


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
        self.observer = SearchObserver()
        self.validate()

    def validate(self):
        s = self.settings
        mode = s.get("evaluation_mode", CYCLIC_SORTIES)
        if mode not in (CYCLIC_SORTIES, SINGLE_WAVE, BUDGET_WAVE):
            raise ValueError("Unknown balance evaluation mode")
        if s.get("version") != VERSION or self.config.data["environment_factory"] != "learning_lab.battle:BattleEnvironment":
            raise ValueError("Requires balance v2 and BattleEnvironment")
        if type(s["candidate_budget"]) is not int or s["candidate_budget"] <= 0:
            raise ValueError("candidate_budget must be positive")
        if type(s["search_seed"]) is not int or s["search_seed"] < 0:
            raise ValueError("search_seed must be nonnegative")
        if not finite(s["change_weight"]) or s["change_weight"] < 0:
            raise ValueError("change_weight must be nonnegative")
        if 'validation_gate' in s:
            ValidationPolicy(s['validation_gate'])
            if s.get('learning_scope') != PER_CHARACTER:
                raise ValueError('Validation policies require per-character learning')
        if type(s.get('always_joint_validation', False)) is not bool:
            raise ValueError('always_joint_validation must be a boolean')
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
            encounter = BudgetWaveEncounter(case) if mode == BUDGET_WAVE else SingleWaveEncounter(case) if mode == SINGLE_WAVE else None
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
                if encounter is not None:
                    limits = case["encounter"]
                    enemies = {d["id"] for d in battle["enemy_baseline"]}
                    if set(limits["ally_pool"]) - definitions.keys() or set(limits["enemy_pool"]) - enemies:
                        raise ValueError("Encounter pools contain undefined characters")
                    if mode == BUDGET_WAVE:
                        _, matchup = encounter.prepare(scenario, seeds[0])
                        enemy_definitions = {d['id']: d for d in battle['enemy_baseline']}
                        if any(enemy_definitions[c] != definitions[c] for c in limits['enemy_pool']):
                            raise ValueError('Budget comparison requires frozen copies of the authored ally definitions')
                        if len(matchup['ally']) + len(matchup['enemy']) > self.config.data['environment_options'].get('units', DEFAULT_UNIT_CAPACITY):
                            raise ValueError('Budget armies exceed the observation capacity')
                    elif limits["ally_count"] > battle["rules"].get("party_size", DEFAULT_PARTY_SIZE):
                        raise ValueError("Opening ally wave exceeds the fixed party capacity")
                    if mode == SINGLE_WAVE and limits["ally_count"] + limits["enemy_count"] > self.config.data["environment_options"].get("units", DEFAULT_UNIT_CAPACITY):
                        raise ValueError("Opening wave exceeds the observation capacity")
                if battle.get("opening_wave") is not None:
                    raise ValueError("Balance templates must leave opening waves to the case sampler")

    @property
    def policy_id(self):
        return SINGLE_WAVE_POLICY_ID if self.settings.get("evaluation_mode") in (SINGLE_WAVE, BUDGET_WAVE) else POLICY_ID

    def evaluate(self, candidate: dict, split: str, *, joint: bool = False):
        config = apply_candidate(self.config, candidate)
        observer = self.observer
        observer.check()
        observer.emit("phase", split=split, joint=joint, candidate=candidate,
            total=len(self.settings["cases"]) * len(config.scenarios(split)) * len(self.settings["seeds"][split]) * (len(LEGS) if self.settings.get("evaluation_mode") == BUDGET_WAVE else 1))
        environment = config.create_environment()
        results = []
        try:
            for case in self.settings["cases"]:
                case = dict(case, freeze_others=False) if joint else case
                mode = self.settings.get('evaluation_mode')
                encounter = BudgetWaveEncounter(case) if mode == BUDGET_WAVE else SingleWaveEncounter(case) if mode == SINGLE_WAVE else None
                episodes = []
                for original in config.scenarios(split):
                    scenario = prepare_case(self.config, original, case)
                    battle = scenario["parameters"]["battle"]
                    for seed in self.settings["seeds"][split]:
                        for leg in LEGS if mode == BUDGET_WAVE else ("forward",):
                            observer.check()
                            current, matchup = encounter.prepare(scenario, seed, leg) if mode == BUDGET_WAVE else encounter.prepare(scenario, seed) if encounter else (scenario, None)
                            obs = environment.reset(current, seed)
                            battle = current["parameters"]["battle"]
                            target_team = matchup.get("target_team", ALLY) if matchup else ALLY
                            observer.emit("episode_started", scenario=current, case=case, seed=seed, matchup=matchup)
                            if observer.enabled:
                                observer.emit("frame", debug=environment.save_state()["debug"], decision=0)
                            required_first = None
                            if case.get("target_first", False):
                                ids = [d["id"] for d in battle["ally_definitions"]]
                                required_first = (ids.index(case["character"]) + 1) / len(ids)
                            policy = ScriptedSortiePolicy(environment.spec,
                                battle["rules"].get("party_size", DEFAULT_PARTY_SIZE), required_first)
                            for decision in range(config.data["evaluation"]["max_decisions"]):
                                observer.check()
                                action = policy.wait if encounter else policy.choose(obs)
                                transition = environment.step(action)
                                obs = transition.observation
                                if observer.should_capture(decision + 1, transition.ended):
                                    observer.emit("frame", debug=environment.save_state()["debug"], decision=decision + 1)
                                if transition.ended:
                                    break
                            else:
                                raise RuntimeError("Balance evaluation watchdog exceeded")
                            state = environment.save_state()["debug"]
                            main_index = next(i for i, b in enumerate(battle["stage"]["bases"]) if (b["main"] if target_team == ALLY else b["boss"]))
                            main_hp = max(0, state["bases"][main_index]["hp"]) / battle["stage"]["bases"][main_index]["hp"]
                            metric = next((m for m in transition.info["metrics"] if m["team"] == target_team and
                                           m["character"] == case["character"]), None)
                            deployed = metric["deployed"] if metric else 0
                            cost = next(d["cost"] for d in battle["ally_definitions"] if d["id"] == case["character"])
                            if not deployed:
                                raise ValueError(f"No exposure for {case['id']}; revise roster, budget or policy")
                            episodes.append({"scenario": scenario["id"], "seed": seed,
                                "encounter": matchup,
                                "weight": scenario["weight"], "outcome": transition.info["outcome"],
                                "deployed": deployed, "metrics": {
                                    "win_rate": float(transition.info["outcome"] == ("defeat" if target_team != ALLY else "victory")),
                                    "main_hp_ratio": main_hp,
                                    "damage_per_energy": metric["damage"] / (deployed * cost),
                                    "survival_fraction": (deployed - metric["deaths"]) / deployed}})
                            if mode == BUDGET_WAVE:
                                episodes[-1]['metrics'].update(battle_score(state, transition.info['metrics'], battle, target_team))
                            observer.emit("episode_completed", episode=episodes[-1])
                total_weight = sum(e["weight"] for e in episodes)
                means = {name: sum(e["metrics"][name] * e["weight"] for e in episodes) / total_weight for name in episodes[0]['metrics']}
                loss = sum(t["weight"] * (max(t["min"] - means[name], means[name] - t["max"], 0) / t["scale"]) ** 2
                           for name, t in case["targets"].items()) / sum(t["weight"] for t in case["targets"].values())
                results.append({"case": case["id"], "role": case["role"], "reference_cost": case["reference_cost"],
                                "loss": loss, "weight": case["weight"], "means": means, "episodes": episodes})
                if self.settings.get('learning_scope') == PER_CHARACTER:
                    results[-1]['character'] = case['character']
        finally:
            environment.close()
        return {"split": split, "loss": sum(c["loss"] * c["weight"] for c in results) / sum(c["weight"] for c in results),
                "cases": results}


class BalanceSearch:
    def __init__(self, config, settings):
        self.config, self.settings = config, settings
        self.evaluator = BalanceEvaluator(config, settings)
        self.strategy = settings.get("search_strategy", RANDOM_STRATEGY)
        if self.strategy not in (RANDOM_STRATEGY, ADAPTIVE_STRATEGY):
            raise ValueError("Unknown parameter search strategy")
        adaptive = self.strategy == ADAPTIVE_STRATEGY
        self.space = (RangeParameterSpace if adaptive else ParameterSpace)(config, settings)
        self.learner = AdaptiveProposalLearner(self.space, settings.get("adaptive", {}),
            settings["search_seed"]) if adaptive else None
        self.per_character = settings.get('learning_scope') == PER_CHARACTER
        if self.per_character:
            if not adaptive or any(p['field'] not in ('hp', 'power', 'defense') for p in settings['parameters']):
                raise ValueError('Per-character learning adjusts only HP, power and defense with fixed costs')
            if not all(case.get('freeze_others') for case in settings['cases']):
                raise ValueError('Independent feedback requires frozen companions')
            self.learner = CharacterProposalLearner(config, settings, self.space)
        covered = {c["character"] for c in settings["cases"]}
        if any(c not in covered for c, _ in self.space.axes):
            raise ValueError("Every adjusted character needs an evaluation case")

    def run(self, directory: Path, observer=None, *, continue_from=None):
        directory = directory.resolve()
        assets = Path(__file__).resolve().parents[2] / "Assets"
        if directory.is_relative_to(assets.resolve()):
            raise ValueError("Balance artifacts must stay outside Assets")
        continuation = SearchContinuation(continue_from, self.config, self.settings, self.learner) if continue_from else None
        repository = ExperimentRepository(directory)
        manifest = {"version": VERSION, "config": self.config.data, "settings": self.settings,
                               "config_hash": self.config.digest, "policy": self.evaluator.policy_id,
                               "evaluation_mode": self.settings.get("evaluation_mode", CYCLIC_SORTIES),
                               "search_strategy": self.strategy,
                               "adaptive_options": self.learner.options if self.learner else None,
                               "search_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                               "parameter_space_code_sha256": hashlib.sha256(Path(__file__).with_name("parameter_space.py").read_bytes()).hexdigest(),
                               "adaptive_code_sha256": hashlib.sha256(Path(__file__).with_name("adaptive_search.py").read_bytes()).hexdigest(),
                               "artifact_code_sha256": hashlib.sha256(Path(__file__).with_name("artifacts.py").read_bytes()).hexdigest(),
                               "monitoring_code_sha256": hashlib.sha256(Path(__file__).with_name("monitoring.py").read_bytes()).hexdigest(),
                               "frame_capture_decisions": getattr(observer, "capture_decisions", None),
                               "frame_archive_stride": getattr(observer, 'archive_stride', None),
                               "cost_balance_code_sha256": hashlib.sha256(Path(__file__).with_name('cost_balance.py').read_bytes()).hexdigest(),
                               "character_search_code_sha256": hashlib.sha256(Path(__file__).with_name('character_search.py').read_bytes()).hexdigest(),
                               "roster_analysis_code_sha256": hashlib.sha256(Path(__file__).with_name('roster_analysis.py').read_bytes()).hexdigest(),
                               "encounter_code_sha256": hashlib.sha256(Path(__file__).with_name("encounters.py").read_bytes()).hexdigest(),
                               "continuation_code_sha256": hashlib.sha256(Path(__file__).with_name('search_continuation.py').read_bytes()).hexdigest()}
        if continuation:
            manifest['continuation'] = continuation.metadata
        repository.initialize(manifest)
        observer = observer or SearchObserver()
        self.evaluator.observer = observer
        bounds = getattr(self.space, "bounds", [(min(values), max(values)) for values in self.space.values])
        observer.emit("started", config=self.config.data, settings=self.settings,
            baseline=self.space.baseline, axes=self.space.axes, bounds=bounds)
        analyzer = None
        if self.settings.get('analysis', {}).get('enabled'):
            from .roster_analysis import RosterAnalyzer
            analyzer = RosterAnalyzer(self.config, self.settings)
            if self.settings['analysis'].get('initial_focused', True):
                analyzer.run(self.space.baseline, observer=observer, parameter_version='baseline', include_pairs=False)
            observer.emit('state', state='running')
        trials = copy.deepcopy(continuation.trials) if continuation else []
        if continuation:
            continuation.copy_trials(directory)
            repository.save_checkpoint({'version': CHECKPOINT_VERSION, 'trial': len(trials) - TRIAL_INCREMENT,
                'config_hash': self.config.digest, 'settings_hash': canonical_hash(self.settings),
                'learner': self.learner.state_dict()})
            observer.emit('continuation', data=continuation.metadata)
            for trial in trials:
                observer.emit('trial_completed', trial=trial)
        candidates = None if self.learner else self.space.candidates(
            self.settings["candidate_budget"], self.settings["search_seed"])
        for index in range(len(trials), self.settings["candidate_budget"]):
            observer.check()
            if self.learner:
                before = self.learner.describe()
                candidate = copy.deepcopy(self.space.baseline) if index == 0 else self.learner.ask()
            else:
                candidate = next(candidates, None)
                if candidate is None:
                    break
            observer.emit("trial_started", index=index, candidate=candidate,
                distribution=self.learner.describe() if self.learner else None)
            evaluation = self.evaluator.evaluate(candidate, "train")
            score = evaluation["loss"] + self.settings["change_weight"] * self.space.change_penalty(candidate)
            character_scores = self.learner.scores(evaluation, candidate, self.settings['change_weight']) if self.per_character else None
            if character_scores is not None:
                score = sum(character_scores.values()) / len(character_scores)
            trial = {"index": index, "candidate": candidate, "candidate_hash": canonical_hash(candidate),
                     "score": score, "evaluation": evaluation}
            if character_scores is not None:
                trial['character_scores'] = character_scores
            if self.learner:
                if index == 0:
                    self.learner.initialize_score(character_scores if self.per_character else score)
                    update = None
                else:
                    update = self.learner.tell(candidate, character_scores if self.per_character else score)
                trial.update(proposal_before=before, proposal_after=self.learner.describe(), update=update)
                repository.save_checkpoint({"version": CHECKPOINT_VERSION, "trial": index,
                    "config_hash": self.config.digest, "settings_hash": canonical_hash(self.settings),
                    "learner": self.learner.state_dict()})
            trials.append(trial)
            write_json(directory / f"trial-{index:04d}.json", trial)
            observer.emit("trial_completed", trial=trial)
            print(json.dumps({"trial": index, "score": score,
                "distribution_updates": self.learner.updates if self.learner else 0}), flush=True)
        best = min(trials, key=lambda t: (t["score"], t["index"]))
        best_by_character = None
        if self.per_character:
            best_by_character = {c: min(trials, key=lambda t: (t['character_scores'][c], t['index'])) for c in self.learner.learners}
            best = {'index': max(t['index'] for t in best_by_character.values()),
                    'candidate': {c: t['candidate'][c] for c, t in best_by_character.items()},
                    'score': sum(t['character_scores'][c] for c, t in best_by_character.items()) / len(best_by_character)}
        baseline_validation = self.evaluator.evaluate(self.space.baseline, "validation")
        selected_validation = (baseline_validation if best["index"] == 0 else
                               self.evaluator.evaluate(best["candidate"], "validation"))
        gate = self.settings.get('validation_gate')
        accepted = best['index'] != 0 and preserved_validation(baseline_validation, selected_validation, gate)
        passed_individual_validation = accepted
        requires_joint = any(case.get("freeze_others", False) for case in self.settings["cases"])
        always_joint = self.settings.get('always_joint_validation', False)
        joint_validation = None
        if (accepted and requires_joint) or always_joint:
            joint_validation = self.evaluator.evaluate(best["candidate"], "validation", joint=True)
            # Baseline values are identical in isolated and joint evaluation.
            joint_accepted = preserved_validation(baseline_validation, joint_validation, gate)
            accepted = best['index'] != 0 and joint_accepted and (accepted or ValidationPolicy(gate).mode == AGGREGATE)
        recommended = best["candidate"] if accepted else self.space.baseline
        # Holdout is reported once, after selection; never used to select a different candidate.
        holdout = (self.evaluator.evaluate(recommended, "holdout", joint=True) if requires_joint else
                   self.evaluator.evaluate(recommended, "holdout"))
        report = {"version": VERSION, "policy": self.evaluator.policy_id, "config_hash": self.config.digest,
                  "evaluation_mode": self.settings.get("evaluation_mode", CYCLIC_SORTIES),
                  "search_strategy": self.strategy,
                  "distribution_updates": self.learner.updates if self.learner else 0,
                  "final_distribution": self.learner.describe() if self.learner else None,
                  "stage_split_mode": self.config.data.get("stage_split_mode", DISJOINT_FAMILIES),
                  "candidate_count": len(trials), "selected_train_candidate": best["candidate"],
                  "baseline_train_score": trials[0]["score"], "selected_train_score": best["score"],
                  "passed_validation_gate": accepted, "recommended_overrides": recommended,
                  "passed_individual_validation": passed_individual_validation,
                  "joint_validation": joint_validation, "holdout_joint": requires_joint,
                  "baseline_validation": baseline_validation, "selected_validation": selected_validation,
                  "holdout": holdout, "applied_to_game": False}
        if continuation:
            report['continuation'] = continuation.metadata
        if best_by_character:
            report.update(learning_scope=PER_CHARACTER,
                selected_train_trials={c: t['index'] for c, t in best_by_character.items()},
                validation_gate=gate, authored_costs={d['id']: d['cost'] for d in self.config.data['scenarios'][0]['parameters']['battle']['ally_definitions']})
        if always_joint:
            report.update(always_joint_validation=True,
                validation_decision=ValidationPolicy(gate).assess(baseline_validation, joint_validation),
                individual_validation_diagnostics=ValidationPolicy(gate).assess(baseline_validation, selected_validation),
                adjusted_characters=sorted(best['candidate']),
                frozen_characters=sorted({d['id'] for d in self.config.data['scenarios'][0]['parameters']['battle']['ally_definitions']} - best['candidate'].keys()))
        write_json(directory / "report.json", report)
        write_json(directory / "recommended-config.json", apply_candidate(self.config, recommended).data)
        if analyzer:
            analyzer.run(recommended, observer=observer, parameter_version='recommended' if recommended != self.space.baseline else 'baseline')
        observer.emit("completed", report=report)
        return report


def main():
    parser = argparse.ArgumentParser(description="Tune character parameters with fixed scripted play")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument('--continue-from', type=Path, help='Inherit a checkpoint and trials into a fresh run')
    args = parser.parse_args()
    settings = json.loads(args.settings.read_text(encoding="utf-8-sig"))
    report = BalanceSearch(ExperimentConfig.load(args.config), settings).run(args.run, continue_from=args.continue_from)
    print(json.dumps({"passed_validation_gate": report["passed_validation_gate"],
                      "recommended_overrides": report["recommended_overrides"]}))


if __name__ == "__main__":
    main()
