import hashlib
import importlib
import json
import math
from copy import deepcopy
from pathlib import Path

from .contracts import LearningEnvironment

CONFIG_VERSION = 1
SPLITS = ("train", "validation", "holdout")
EVALUATION_SPLITS = ("validation", "holdout")
DISJOINT_FAMILIES = "disjoint_families"
SHARED_LAYOUTS = "shared_layouts"


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ExperimentConfig:
    def __init__(self, data: dict):
        self.data = deepcopy(data)
        self.validate()

    @classmethod
    def load(cls, path: Path) -> "ExperimentConfig":
        return cls(json.loads(path.read_text(encoding="utf-8-sig")))

    @property
    def digest(self) -> str:
        return canonical_hash(self.data)

    def scenarios(self, split: str) -> list[dict]:
        if split not in SPLITS:
            raise ValueError(f"Unknown split: {split}")
        return [s for s in self.data["scenarios"] if s["split"] == split]

    def evaluation_seeds(self, split: str) -> list[int]:
        if split not in SPLITS:
            raise ValueError(f"Unknown split: {split}")
        evaluation = self.data["evaluation"]
        return evaluation.get("split_seeds", {}).get(split, evaluation.get("seeds", []))

    @property
    def reserved_evaluation_seeds(self) -> frozenset[int]:
        if self.data.get("stage_split_mode", DISJOINT_FAMILIES) != SHARED_LAYOUTS:
            return frozenset()
        return frozenset(seed for split in EVALUATION_SPLITS for seed in self.evaluation_seeds(split))

    def create_environment(self) -> LearningEnvironment:
        module_name, class_name = self.data["environment_factory"].split(":")
        factory = getattr(importlib.import_module(module_name), class_name)
        environment = factory(**self.data.get("environment_options", {}))
        if not isinstance(environment, LearningEnvironment):
            raise TypeError("Factory must create a LearningEnvironment")
        environment.spec.validate()
        return environment

    def validate(self) -> None:
        if self.data.get("version") != CONFIG_VERSION:
            raise ValueError("Unsupported experiment config version")
        if len(self.data["environment_factory"].split(":")) != 2:
            raise ValueError("environment_factory must be module:Class")
        mode = self.data.get("stage_split_mode", DISJOINT_FAMILIES)
        if mode not in (DISJOINT_FAMILIES, SHARED_LAYOUTS):
            raise ValueError("Unknown stage_split_mode")
        seen = set()
        families: dict[str, str] = {}
        parameters_by_family = {}
        for scenario in self.data["scenarios"]:
            if scenario["id"] in seen:
                raise ValueError("Scenario IDs must be unique")
            seen.add(scenario["id"])
            if scenario["split"] not in SPLITS:
                raise ValueError("Unknown scenario split")
            family = scenario["family"]
            if mode == DISJOINT_FAMILIES and family in families and families[family] != scenario["split"]:
                raise ValueError("A stage family cannot leak across data splits")
            if mode == SHARED_LAYOUTS:
                digest = canonical_hash(scenario["parameters"])
                if family in parameters_by_family and parameters_by_family[family] != digest:
                    raise ValueError("Shared-layout scenarios must have identical parameters across splits")
                parameters_by_family[family] = digest
            families[family] = scenario["split"]
            weight = scenario["weight"]
            if not math.isfinite(weight) or weight <= 0:
                raise ValueError("Scenario weights must be finite and positive")
        if not self.scenarios("train"):
            raise ValueError("At least one training scenario is required")
        if mode == SHARED_LAYOUTS:
            expected = {s["family"] for s in self.scenarios("train")}
            for split in SPLITS:
                entries = self.scenarios(split)
                if {s["family"] for s in entries} != expected or len(entries) != len(expected):
                    raise ValueError("Shared-layout splits must contain exactly the same layout families once each")
        if type(self.data["seed"]) is not int or self.data["seed"] < 0:
            raise ValueError("Training seed must be a nonnegative integer")
        ppo = self.data["ppo"]
        for name in ("rollout_steps", "epochs", "minibatch_size", "hidden_size",
                     "torch_threads"):
            if type(ppo[name]) is not int or ppo[name] <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("learning_rate", "clip_range", "max_grad_norm", "epsilon"):
            if not math.isfinite(ppo[name]) or ppo[name] <= 0:
                raise ValueError(f"Invalid {name}")
        for name in ("gamma", "gae_lambda"):
            if not 0 <= ppo[name] <= 1:
                raise ValueError(f"{name} must be within [0, 1]")
        for name in ("entropy_coefficient", "value_coefficient"):
            if not math.isfinite(ppo[name]) or ppo[name] < 0:
                raise ValueError(f"Invalid {name}")
        if ppo["minibatch_size"] > ppo["rollout_steps"]:
            raise ValueError("minibatch_size must not exceed rollout_steps")
        if self.data["evaluation"]["max_decisions"] <= 0:
            raise ValueError("Evaluation watchdog must be positive")
        split_seeds = self.data["evaluation"].get("split_seeds", {})
        if set(split_seeds) - set(EVALUATION_SPLITS):
            raise ValueError("split_seeds accepts validation and holdout only")
        if mode == SHARED_LAYOUTS and set(split_seeds) != set(EVALUATION_SPLITS):
            raise ValueError("Shared layouts require explicit disjoint validation/holdout split_seeds")
        for split in EVALUATION_SPLITS:
            seeds = self.evaluation_seeds(split)
            if not seeds or any(type(seed) is not int or seed < 0 for seed in seeds):
                raise ValueError("Evaluation seeds must be nonempty nonnegative integers")
            if len(seeds) != len(set(seeds)):
                raise ValueError("Duplicate evaluation seeds would inflate the trial count")
        if split_seeds and set(self.evaluation_seeds("validation")) & set(self.evaluation_seeds("holdout")):
            raise ValueError("Validation and holdout seeds must be disjoint")


class ScenarioSampler:
    def __init__(self, scenarios: list[dict], seed: int, excluded_seeds=()):
        import numpy as np
        self.scenarios = scenarios
        self.rng = np.random.default_rng(seed)
        self.excluded_seeds = frozenset(excluded_seeds)
        if len(self.excluded_seeds) >= np.iinfo(np.int32).max:
            raise ValueError("No training seeds remain")
        weights = np.array([s["weight"] for s in scenarios], dtype=np.float64)
        self.probabilities = weights / weights.sum()

    def next(self) -> tuple[dict, int]:
        import numpy as np
        index = int(self.rng.choice(len(self.scenarios), p=self.probabilities))
        seed = int(self.rng.integers(np.iinfo(np.int32).max))
        while seed in self.excluded_seeds:
            seed = int(self.rng.integers(np.iinfo(np.int32).max))
        return self.scenarios[index], seed
