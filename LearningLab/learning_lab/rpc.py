"""Optional JSON-line bridge. The future game CLI implements this boundary."""
import json
import queue
import subprocess
import threading
from dataclasses import asdict

import numpy as np

from .contracts import (
    CONTRACT_VERSION, EnvironmentSpec, LearningEnvironment, Observation, Transition)

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_MESSAGE_CHARACTERS = 16 * 1024 * 1024


def encode_observation(observation: Observation) -> dict:
    return {"values": observation.values.tolist(),
            "action_mask": observation.action_mask.tolist()}


def decode_observation(data: dict) -> Observation:
    mask = data["action_mask"]
    if not all(type(value) is bool for value in mask):
        raise ValueError("Wire action_mask must contain booleans")
    return Observation(np.asarray(data["values"], dtype=np.float32),
                       np.asarray(mask, dtype=np.bool_))


class JsonProcessEnvironment(LearningEnvironment):
    def __init__(self, command: list[str], timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        if not command or timeout_seconds <= 0:
            raise ValueError("A command and positive timeout are required")
        self.timeout = timeout_seconds
        self.request_id = 0
        self.responses: queue.Queue = queue.Queue()
        self.closed = False
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            # stderr is inherited so a verbose child cannot block on a full stderr pipe.
            text=True, encoding="utf-8", bufsize=1, creationflags=flags)
        self.reader = threading.Thread(target=self._read_responses, daemon=True)
        self.reader.start()
        try:
            data = self._request("spec", {})
            self._spec = EnvironmentSpec(
                int(data["observation_size"]), tuple(data["action_names"]), data["schema_id"])
            self._spec.validate()
        except Exception:
            self.close()
            raise

    def _read_responses(self) -> None:
        try:
            while True:
                line = self.process.stdout.readline(MAX_MESSAGE_CHARACTERS + 1)
                if not line:
                    self.responses.put(RuntimeError("Environment process closed stdout"))
                    return
                if len(line) > MAX_MESSAGE_CHARACTERS:
                    raise ValueError("Environment response is too large")
                self.responses.put(line)
        except Exception as error:
            self.responses.put(error)

    def _request(self, operation: str, payload: dict) -> dict:
        if self.closed:
            raise RuntimeError("Environment is closed")
        self.request_id += 1
        request = {"version": CONTRACT_VERSION, "id": self.request_id,
                   "operation": operation, "payload": payload}
        try:
            self.process.stdin.write(json.dumps(request, allow_nan=False) + "\n")
            self.process.stdin.flush()
            response = self.responses.get(timeout=self.timeout)
            if isinstance(response, Exception):
                raise response
            message = json.loads(response)
            if message["version"] != CONTRACT_VERSION or message["id"] != self.request_id:
                raise ValueError("Protocol version/request ID mismatch")
            if "error" in message:
                raise RuntimeError(f"Environment error: {message['error']}")
            return message["result"]
        except Exception:
            self.close()
            raise

    @property
    def spec(self) -> EnvironmentSpec:
        return self._spec

    def reset(self, scenario: dict, seed: int) -> Observation:
        observation = decode_observation(self._request(
            "reset", {"scenario": scenario, "seed": seed}))
        observation.validate(self.spec)
        return observation

    def step(self, action: int) -> Transition:
        data = self._request("step", {"action": action})
        if type(data["terminated"]) is not bool or type(data["truncated"]) is not bool:
            raise ValueError("Wire termination flags must be booleans")
        transition = Transition(
            decode_observation(data["observation"]), float(data["reward"]),
            data["terminated"], data["truncated"], data["info"])
        transition.validate(self.spec)
        return transition

    def save_state(self) -> dict:
        return self._request("save_state", {})

    def load_state(self, state: dict) -> Observation:
        observation = decode_observation(self._request("load_state", {"state": state}))
        observation.validate(self.spec)
        return observation

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.reader.join(timeout=self.timeout)
        self.process.stdin.close()
        self.process.stdout.close()


class EnvironmentServer:
    def __init__(self, environment: LearningEnvironment):
        self.environment = environment

    def dispatch(self, operation: str, payload: dict) -> dict:
        if operation == "spec":
            return asdict(self.environment.spec)
        if operation == "reset":
            return encode_observation(self.environment.reset(
                payload["scenario"], payload["seed"]))
        if operation == "step":
            transition = self.environment.step(payload["action"])
            transition.validate(self.environment.spec)
            return {"observation": encode_observation(transition.observation),
                    "reward": transition.reward, "terminated": transition.terminated,
                    "truncated": transition.truncated, "info": transition.info}
        if operation == "save_state":
            return self.environment.save_state()
        if operation == "load_state":
            return encode_observation(self.environment.load_state(payload["state"]))
        raise ValueError(f"Unknown operation: {operation}")

    def serve(self, source, destination) -> None:
        try:
            for line in source:
                request = {}
                try:
                    request = json.loads(line)
                    if request["version"] != CONTRACT_VERSION:
                        raise ValueError("Unsupported protocol version")
                    result = self.dispatch(request["operation"], request["payload"])
                    response = {"result": result}
                except Exception as error:
                    response = {"error": str(error)}
                response.update({"version": CONTRACT_VERSION, "id": request.get("id")})
                destination.write(json.dumps(response, allow_nan=False) + "\n")
                destination.flush()
        finally:
            self.environment.close()
