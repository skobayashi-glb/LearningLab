import json
import platform
from dataclasses import asdict
from pathlib import Path

import numpy as np
import torch

from . import __version__
from .artifacts import CHECKPOINT_VERSION, ExperimentRepository
from .config import ExperimentConfig, ScenarioSampler, canonical_hash
from .contracts import CONTRACT_VERSION, Observation
from .policy import MaskedActorCritic
from .ppo import PpoOptimizer, RolloutBatch, generalized_advantages


class PpoTrainer:
    def __init__(self, config: ExperimentConfig):
        self.config = config
        torch.set_num_threads(config.data["ppo"]["torch_threads"])
        torch.use_deterministic_algorithms(True)
        torch.manual_seed(config.data["seed"])
        self.environment = config.create_environment()
        self.policy = MaskedActorCritic(
            self.environment.spec, config.data["ppo"]["hidden_size"])
        self.updater = PpoOptimizer(self.policy, config.data["ppo"])
        self.sampler = ScenarioSampler(config.scenarios("train"), config.data["seed"])
        self.decisions = 0
        self.updates = 0
        self.episodes = 0
        self.episode_return = 0.0
        self.episode_steps = 0
        self.observation = self._reset()

    def _reset(self) -> Observation:
        scenario, seed = self.sampler.next()
        observation = self.environment.reset(scenario, seed)
        observation.validate(self.environment.spec)
        return observation

    def manifest(self) -> dict:
        return {
            "tool_version": __version__, "contract_version": CONTRACT_VERSION,
            "config": self.config.data, "config_hash": self.config.digest,
            "environment_schema": asdict(self.environment.spec),
            "python": platform.python_version(), "torch": str(torch.__version__),
            "numpy": np.__version__, "device": "cpu",
            "scope": "Independent development tool; demo results are not game balance results"}

    def collect(self, count: int) -> tuple[RolloutBatch, list[dict]]:
        observations, masks, actions, logs = [], [], [], []
        rewards, values, next_values, terminated, truncated = [], [], [], [], []
        discount_steps = []
        completed = []
        self.policy.eval()
        for _ in range(count):
            observation = self.observation
            with torch.no_grad():
                distribution, value = self.policy.distribution_and_value(
                    torch.from_numpy(observation.values).unsqueeze(0),
                    torch.from_numpy(observation.action_mask).unsqueeze(0))
                action = distribution.sample()
                log_probability = distribution.log_prob(action)
            transition = self.environment.step(int(action.item()))
            transition.validate(self.environment.spec)
            with torch.no_grad():
                next_value = self.policy.value(
                    torch.from_numpy(transition.observation.values).unsqueeze(0))
            observations.append(observation.values.copy())
            masks.append(observation.action_mask.copy())
            actions.append(action.item())
            logs.append(log_probability.item())
            rewards.append(transition.reward)
            values.append(value.item())
            next_values.append(next_value.item())
            terminated.append(transition.terminated)
            truncated.append(transition.truncated)
            elapsed = transition.info.get("discount_steps", 1.0)
            if not np.isfinite(elapsed) or elapsed < 0:
                raise ValueError("discount_steps must be finite and nonnegative")
            discount_steps.append(elapsed)
            self.decisions += 1
            self.episode_return += transition.reward
            self.episode_steps += 1
            if transition.ended:
                self.episodes += 1
                completed.append({
                    "return": self.episode_return, "length": self.episode_steps,
                    "outcome": transition.info["outcome"],
                    "scenario_id": transition.info.get("scenario_id")})
                self.episode_return = 0.0
                self.episode_steps = 0
                self.observation = self._reset()
            else:
                self.observation = transition.observation
        advantages, returns = generalized_advantages(
            torch.tensor(rewards, dtype=torch.float32),
            torch.tensor(values, dtype=torch.float32),
            torch.tensor(next_values, dtype=torch.float32),
            torch.tensor(terminated, dtype=torch.bool),
            torch.tensor(truncated, dtype=torch.bool),
            self.config.data["ppo"]["gamma"], self.config.data["ppo"]["gae_lambda"],
            torch.tensor(discount_steps, dtype=torch.float32))
        return RolloutBatch(
            torch.from_numpy(np.stack(observations)),
            torch.from_numpy(np.stack(masks)),
            torch.tensor(actions, dtype=torch.long),
            torch.tensor(logs, dtype=torch.float32), advantages, returns), completed

    def checkpoint(self) -> dict:
        # JSON roundtrip enforces the adapter's portable-state contract.
        environment_state = json.loads(json.dumps(
            self.environment.save_state(), allow_nan=False))
        return {
            "version": CHECKPOINT_VERSION,
            "manifest": self.manifest(),
            "policy": self.policy.state_dict(),
            "optimizer": self.updater.optimizer.state_dict(),
            "torch_rng": torch.get_rng_state(),
            "sampler_rng": self.sampler.rng.bit_generator.state,
            "environment_state": environment_state,
            "observation": torch.from_numpy(self.observation.values.copy()),
            "mask": torch.from_numpy(self.observation.action_mask.copy()),
            "decisions": self.decisions, "updates": self.updates, "episodes": self.episodes,
            "episode_return": self.episode_return, "episode_steps": self.episode_steps}

    def restore(self, checkpoint: dict) -> None:
        manifest = checkpoint["manifest"]
        if manifest["config_hash"] != self.config.digest:
            raise ValueError("Resume requires the identical configuration")
        if canonical_hash(manifest["environment_schema"]) != canonical_hash(
                asdict(self.environment.spec)):
            raise ValueError("Environment schema has changed")
        current = self.manifest()
        for field in ("torch", "numpy", "python", "tool_version", "contract_version"):
            if manifest[field] != current[field]:
                raise ValueError(f"Resume requires the same {field}")
        self.policy.load_state_dict(checkpoint["policy"])
        self.updater.optimizer.load_state_dict(checkpoint["optimizer"])
        self.observation = self.environment.load_state(checkpoint["environment_state"])
        self.observation.validate(self.environment.spec)
        if not np.array_equal(self.observation.values, checkpoint["observation"].numpy()):
            raise ValueError("Restored environment observation differs from checkpoint")
        if not np.array_equal(self.observation.action_mask, checkpoint["mask"].numpy()):
            raise ValueError("Restored action mask differs from checkpoint")
        self.sampler.rng.bit_generator.state = checkpoint["sampler_rng"]
        for name in ("decisions", "updates", "episodes", "episode_return", "episode_steps"):
            setattr(self, name, checkpoint[name])
        torch.set_rng_state(checkpoint["torch_rng"])

    def train(self, total_decisions: int, repository: ExperimentRepository) -> Path:
        if total_decisions <= self.decisions:
            raise ValueError("Target decisions must exceed the saved training progress")
        while self.decisions < total_decisions:
            count = min(self.config.data["ppo"]["rollout_steps"],
                        total_decisions - self.decisions)
            batch, completed = self.collect(count)
            self.policy.train()
            metrics = self.updater.update(batch)
            self.updates += 1
            metrics.update({"decisions": self.decisions, "updates": self.updates,
                            "episodes": self.episodes, "completed": completed})
            repository.save_checkpoint(self.checkpoint())
            repository.append_metrics(metrics)
            print(json.dumps({k: v for k, v in metrics.items() if k != "completed"}),
                  flush=True)
        return repository.directory / "checkpoint.pt"

    def close(self) -> None:
        self.environment.close()
