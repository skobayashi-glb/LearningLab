import torch
from torch import nn
from torch.distributions import Categorical

from .contracts import EnvironmentSpec

ACTOR_OUTPUT_GAIN = 0.01
CRITIC_OUTPUT_GAIN = 1.0
HIDDEN_GAIN = 2.0 ** 0.5


class MaskedActorCritic(nn.Module):
    """Generic feed-forward discrete policy; knows no game-specific action names."""

    def __init__(self, spec: EnvironmentSpec, hidden_size: int):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(spec.observation_size, hidden_size), nn.Tanh(),
            nn.Linear(hidden_size, hidden_size), nn.Tanh())
        self.actor = nn.Linear(hidden_size, spec.action_count)
        self.critic = nn.Linear(hidden_size, 1)
        for layer in self.encoder:
            if isinstance(layer, nn.Linear):
                nn.init.orthogonal_(layer.weight, gain=HIDDEN_GAIN)
                nn.init.zeros_(layer.bias)
        for layer, gain in ((self.actor, ACTOR_OUTPUT_GAIN), (self.critic, CRITIC_OUTPUT_GAIN)):
            nn.init.orthogonal_(layer.weight, gain=gain)
            nn.init.zeros_(layer.bias)

    def distribution_and_value(self, observations: torch.Tensor,
                               masks: torch.Tensor) -> tuple[Categorical, torch.Tensor]:
        if masks.dtype != torch.bool or not masks.any(dim=-1).all():
            raise ValueError("Every observation requires a nonempty boolean action mask")
        encoded = self.encoder(observations)
        logits = self.actor(encoded)
        masked = logits.masked_fill(~masks, torch.finfo(logits.dtype).min)
        return Categorical(logits=masked), self.critic(encoded).squeeze(-1)

    def value(self, observations: torch.Tensor) -> torch.Tensor:
        return self.critic(self.encoder(observations)).squeeze(-1)
