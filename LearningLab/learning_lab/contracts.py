"""Versioned boundary implemented by any future game adapter."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np

CONTRACT_VERSION = 1


@dataclass(frozen=True)
class EnvironmentSpec:
    observation_size: int
    action_names: tuple[str, ...]
    schema_id: str

    @property
    def action_count(self) -> int:
        return len(self.action_names)

    def validate(self) -> None:
        if self.observation_size <= 0 or not self.action_names or not self.schema_id:
            raise ValueError("Environment schema must have observations, actions and an ID")
        if len(set(self.action_names)) != self.action_count:
            raise ValueError("Action names must be unique")


@dataclass
class Observation:
    values: np.ndarray
    action_mask: np.ndarray

    def validate(self, spec: EnvironmentSpec) -> None:
        if self.values.shape != (spec.observation_size,):
            raise ValueError("Observation shape does not match schema")
        if self.values.dtype != np.float32:
            raise ValueError("Observation values must be float32")
        if not np.isfinite(self.values).all():
            raise ValueError("Observation contains non-finite values")
        if self.action_mask.shape != (spec.action_count,):
            raise ValueError("Action mask shape does not match schema")
        if self.action_mask.dtype != np.bool_ or not self.action_mask.any():
            raise ValueError("Action mask must be boolean and allow at least one action")


@dataclass
class Transition:
    observation: Observation
    reward: float
    terminated: bool
    truncated: bool
    info: dict[str, Any]

    def validate(self, spec: EnvironmentSpec) -> None:
        self.observation.validate(spec)
        if not np.isfinite(self.reward):
            raise ValueError("Reward must be finite")
        if self.terminated and self.truncated:
            raise ValueError("Use termination when both termination and time limit occur")
        if self.terminated or self.truncated:
            if not isinstance(self.info.get("outcome"), str):
                raise ValueError("Finished transitions must report info.outcome")

    @property
    def ended(self) -> bool:
        return self.terminated or self.truncated


class LearningEnvironment(ABC):
    """Discrete action adapter. An action is atomic; reset must never be implicit."""

    @property
    @abstractmethod
    def spec(self) -> EnvironmentSpec: ...

    @abstractmethod
    def reset(self, scenario: dict[str, Any], seed: int) -> Observation: ...

    @abstractmethod
    def step(self, action: int) -> Transition: ...

    @abstractmethod
    def save_state(self) -> dict[str, Any]:
        """Return a JSON-serializable complete state, including environment RNG."""
        ...

    @abstractmethod
    def load_state(self, state: dict[str, Any]) -> Observation: ...

    def close(self) -> None:
        pass
