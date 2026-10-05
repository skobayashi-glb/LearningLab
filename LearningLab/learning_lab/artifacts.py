import json
import os
from pathlib import Path

import torch

CHECKPOINT_VERSION = 1
TEMPORARY_SUFFIX = ".tmp"


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + TEMPORARY_SUFFIX)
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8")
    os.replace(temporary, path)


class ExperimentRepository:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()

    def initialize(self, manifest: dict) -> None:
        if self.directory.exists() and any(self.directory.iterdir()):
            raise FileExistsError("Run directory is not empty; use resume or a new directory")
        self.directory.mkdir(parents=True, exist_ok=True)
        write_json(self.directory / "manifest.json", manifest)

    def append_metrics(self, metrics: dict) -> None:
        with (self.directory / "metrics.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(metrics, ensure_ascii=False, allow_nan=False) + "\n")

    def save_checkpoint(self, data: dict) -> Path:
        path = self.directory / "checkpoint.pt"
        temporary = path.with_name(path.name + TEMPORARY_SUFFIX)
        torch.save(data, temporary)
        os.replace(temporary, path)
        return path

    @staticmethod
    def load_checkpoint(path: Path) -> dict:
        # Tensor/basic-type payload only; never allow arbitrary pickle execution.
        data = torch.load(path, map_location="cpu", weights_only=True)
        if data["version"] != CHECKPOINT_VERSION:
            raise ValueError("Unsupported checkpoint version")
        return data
