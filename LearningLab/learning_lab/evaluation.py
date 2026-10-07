from collections import Counter
from dataclasses import asdict

import numpy as np
import torch

from .config import DISJOINT_FAMILIES, ExperimentConfig, canonical_hash
from .contracts import Observation
from .policy import MaskedActorCritic

REPLAY_VERSION = 1


def observation_record(observation: Observation) -> dict:
    return {"values": observation.values.tolist(),
            "action_mask": observation.action_mask.tolist()}


class PolicyEvaluator:
    def __init__(self, config: ExperimentConfig, policy: MaskedActorCritic | None):
        self.config = config
        self.policy = policy

    def evaluate(self, split: str, baseline: str = "policy") -> dict:
        scenarios = self.config.scenarios(split)
        if not scenarios:
            raise ValueError(f"No scenarios in {split}")
        if baseline not in ("policy", "random", "first-legal"):
            raise ValueError("Unknown evaluation policy")
        if baseline == "policy" and self.policy is None:
            raise ValueError("A checkpoint is required")
        episodes = []
        environment = self.config.create_environment()
        try:
            for scenario in scenarios:
                for seed in self.config.evaluation_seeds(split):
                    rng = np.random.default_rng(seed)
                    observation = environment.reset(scenario, seed)
                    observation.validate(environment.spec)
                    initial_state = environment.save_state()
                    steps = []
                    episode_return = 0.0
                    for _ in range(self.config.data["evaluation"]["max_decisions"]):
                        legal = np.flatnonzero(observation.action_mask)
                        if baseline == "random":
                            action = int(rng.choice(legal))
                        elif baseline == "first-legal":
                            action = int(legal[0])
                        else:
                            with torch.no_grad():
                                distribution, _ = self.policy.distribution_and_value(
                                    torch.from_numpy(observation.values).unsqueeze(0),
                                    torch.from_numpy(observation.action_mask).unsqueeze(0))
                                action = int(distribution.logits.argmax(dim=-1).item())
                        transition = environment.step(action)
                        transition.validate(environment.spec)
                        episode_return += transition.reward
                        steps.append({
                            "action": action, "reward": transition.reward,
                            "terminated": transition.terminated,
                            "truncated": transition.truncated,
                            "observation": observation_record(transition.observation),
                            "state_hash": canonical_hash(environment.save_state())})
                        observation = transition.observation
                        if transition.ended:
                            break
                    else:
                        raise RuntimeError(
                            "Evaluation watchdog exceeded; adapter must report truncation")
                    episodes.append({
                        "scenario_id": scenario["id"], "seed": seed,
                        "return": episode_return, "length": len(steps),
                        "outcome": transition.info["outcome"],
                        "info": transition.info,
                        "terminated": transition.terminated, "truncated": transition.truncated,
                        "replay": {"version": REPLAY_VERSION,
                                   "config_hash": self.config.digest,
                                   "environment_schema": asdict(environment.spec),
                                   "initial_state": initial_state, "steps": steps}})
        finally:
            environment.close()
        counts = Counter(episode["outcome"] for episode in episodes)
        return {
            "config_hash": self.config.digest, "split": split, "policy": baseline,
            "stage_split_mode": self.config.data.get("stage_split_mode", DISJOINT_FAMILIES),
            "episode_count": len(episodes), "outcomes": dict(counts),
            "mean_return": float(np.mean([e["return"] for e in episodes])),
            "mean_length": float(np.mean([e["length"] for e in episodes])),
            "truncation_rate": float(np.mean([e["truncated"] for e in episodes])),
            "episodes": episodes}


def verify_replay(config: ExperimentConfig, replay: dict) -> int:
    if replay["version"] != REPLAY_VERSION or replay["config_hash"] != config.digest:
        raise ValueError("Replay version/configuration mismatch")
    environment = config.create_environment()
    try:
        if canonical_hash(asdict(environment.spec)) != canonical_hash(
                replay["environment_schema"]):
            raise ValueError("Replay environment schema mismatch")
        environment.load_state(replay["initial_state"]).validate(environment.spec)
        for index, expected in enumerate(replay["steps"]):
            actual = environment.step(expected["action"])
            actual.validate(environment.spec)
            record = {
                "action": expected["action"], "reward": actual.reward,
                "terminated": actual.terminated, "truncated": actual.truncated,
                "observation": observation_record(actual.observation),
                "state_hash": canonical_hash(environment.save_state())}
            if canonical_hash(record) != canonical_hash(expected):
                raise ValueError(f"Replay diverged at decision {index}")
        return len(replay["steps"])
    finally:
        environment.close()
