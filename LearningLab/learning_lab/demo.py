"""A navigation-only test fixture, NOT a TowerDefense simulator."""
from copy import deepcopy
from enum import IntEnum

import numpy as np

from .contracts import EnvironmentSpec, LearningEnvironment, Observation, Transition

DEMO_STATE_VERSION = 1
OBSERVATION_SIZE = 3
MINIMUM_LENGTH = 3
EDGE_INDEX = 0
WIN_REWARD = 1.0
STEP_REWARD = 0.0


class NavigationAction(IntEnum):
    WAIT = 0
    LEFT = 1
    RIGHT = 2


class NavigationDemo(LearningEnvironment):
    """Find a visible target on a line. Sparse reward and legal-action masks."""

    @property
    def spec(self) -> EnvironmentSpec:
        return EnvironmentSpec(
            OBSERVATION_SIZE, ("wait", "left", "right"), "navigation-demo-v1")

    def reset(self, scenario: dict, seed: int) -> Observation:
        self.scenario = deepcopy(scenario)
        params = scenario["parameters"]
        self.length = int(params["length"])
        self.limit = int(params["max_steps"])
        if self.length < MINIMUM_LENGTH or self.limit <= 0:
            raise ValueError("Invalid navigation demo parameters")
        self.rng = np.random.default_rng(seed)
        self.position = int(self.rng.integers(self.length))
        choices = [index for index in range(self.length) if index != self.position]
        self.target = int(self.rng.choice(choices))
        self.steps = 0
        self.ended = False
        return self._observe()

    def _observe(self) -> Observation:
        scale = self.length - 1
        values = np.array([
            self.position / scale, self.target / scale, self.steps / self.limit
        ], dtype=np.float32)
        mask = np.array([
            True, self.position > EDGE_INDEX, self.position < scale
        ], dtype=np.bool_)
        return Observation(values, mask)

    def step(self, action: int) -> Transition:
        if self.ended:
            raise RuntimeError("Call reset before stepping a finished episode")
        mask = self._observe().action_mask
        if action < 0 or action >= len(mask) or not mask[action]:
            raise ValueError(f"Illegal action: {action}")
        if action == NavigationAction.LEFT:
            self.position -= 1
        elif action == NavigationAction.RIGHT:
            self.position += 1
        self.steps += 1
        terminated = self.position == self.target
        truncated = self.steps >= self.limit and not terminated
        self.ended = terminated or truncated
        return Transition(
            self._observe(), WIN_REWARD if terminated else STEP_REWARD,
            terminated, truncated,
            {"outcome": "success" if terminated else "timeout" if truncated else "running",
             "scenario_id": self.scenario["id"]})

    def save_state(self) -> dict:
        return deepcopy({
            "version": DEMO_STATE_VERSION, "scenario": self.scenario,
            "position": self.position, "target": self.target, "steps": self.steps,
            "ended": self.ended, "rng": self.rng.bit_generator.state})

    def load_state(self, state: dict) -> Observation:
        if state["version"] != DEMO_STATE_VERSION:
            raise ValueError("Unsupported demo state")
        self.reset(state["scenario"], seed=0)
        self.position = state["position"]
        self.target = state["target"]
        self.steps = state["steps"]
        self.ended = state["ended"]
        self.rng.bit_generator.state = deepcopy(state["rng"])
        return self._observe()
