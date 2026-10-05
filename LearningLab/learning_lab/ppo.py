from dataclasses import dataclass

import torch

from .policy import MaskedActorCritic

NORMALIZATION_MINIMUM_COUNT = 2
SQUARED_ERROR_WEIGHT = 0.5


def generalized_advantages(rewards: torch.Tensor, values: torch.Tensor,
                           next_values: torch.Tensor, terminated: torch.Tensor,
                           truncated: torch.Tensor, gamma: float,
                           gae_lambda: float,
                           discount_steps: torch.Tensor | None = None
                           ) -> tuple[torch.Tensor, torch.Tensor]:
    """Bootstrap time limits, but never propagate GAE into a newly reset episode."""
    advantages = torch.zeros_like(rewards)
    accumulator = torch.zeros((), dtype=rewards.dtype)
    for index in reversed(range(len(rewards))):
        bootstrap = (~terminated[index]).to(rewards.dtype)
        continuation = (~(terminated[index] | truncated[index])).to(rewards.dtype)
        elapsed = discount_steps[index].item() if discount_steps is not None else 1.0
        discount = gamma ** elapsed
        trace = gae_lambda ** elapsed
        delta = rewards[index] + discount * next_values[index] * bootstrap - values[index]
        accumulator = delta + discount * trace * continuation * accumulator
        advantages[index] = accumulator
    return advantages, advantages + values


@dataclass
class RolloutBatch:
    observations: torch.Tensor
    masks: torch.Tensor
    actions: torch.Tensor
    log_probabilities: torch.Tensor
    advantages: torch.Tensor
    returns: torch.Tensor


class PpoOptimizer:
    def __init__(self, policy: MaskedActorCritic, settings: dict):
        self.policy = policy
        self.settings = settings
        self.optimizer = torch.optim.Adam(
            policy.parameters(), lr=settings["learning_rate"], eps=settings["epsilon"])

    def update(self, batch: RolloutBatch) -> dict[str, float]:
        settings = self.settings
        count = len(batch.actions)
        advantages = batch.advantages
        if count >= NORMALIZATION_MINIMUM_COUNT:
            advantages = (advantages - advantages.mean()) / (
                advantages.std(unbiased=False) + settings["epsilon"])
        totals = {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0}
        updates = 0
        for _ in range(settings["epochs"]):
            permutation = torch.randperm(count)
            for offset in range(0, count, settings["minibatch_size"]):
                indices = permutation[offset:offset + settings["minibatch_size"]]
                distribution, values = self.policy.distribution_and_value(
                    batch.observations[indices], batch.masks[indices])
                logs = distribution.log_prob(batch.actions[indices])
                ratio = (logs - batch.log_probabilities[indices]).exp()
                unclipped = ratio * advantages[indices]
                clipped = ratio.clamp(
                    1.0 - settings["clip_range"], 1.0 + settings["clip_range"]
                ) * advantages[indices]
                actor_loss = -torch.minimum(unclipped, clipped).mean()
                value_loss = SQUARED_ERROR_WEIGHT * (
                    values - batch.returns[indices]).square().mean()
                entropy = distribution.entropy().mean()
                loss = (actor_loss + settings["value_coefficient"] * value_loss
                        - settings["entropy_coefficient"] * entropy)
                if not torch.isfinite(loss):
                    raise FloatingPointError("Non-finite PPO loss")
                self.optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    self.policy.parameters(), settings["max_grad_norm"],
                    error_if_nonfinite=True)
                self.optimizer.step()
                totals["policy_loss"] += actor_loss.item()
                totals["value_loss"] += value_loss.item()
                totals["entropy"] += entropy.item()
                updates += 1
        return {name: value / updates for name, value in totals.items()}
