import argparse
import hashlib
import json
from pathlib import Path

from .artifacts import ExperimentRepository, write_json
from .config import ExperimentConfig
from .evaluation import PolicyEvaluator, verify_replay
from .trainer import PpoTrainer


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent development-only PPO lab")
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train")
    train.add_argument("--config", type=Path, required=True)
    train.add_argument("--run", type=Path, required=True)
    train.add_argument("--steps", type=int, required=True, help="Total decision count")
    train.add_argument("--resume", type=Path)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--config", type=Path, required=True)
    evaluate.add_argument("--checkpoint", type=Path)
    evaluate.add_argument("--split", choices=("validation", "holdout"), default="validation")
    evaluate.add_argument("--baseline", choices=("policy", "random", "first-legal"),
                          default="policy")
    evaluate.add_argument("--output", type=Path, required=True)
    replay = commands.add_parser("replay")
    replay.add_argument("--config", type=Path, required=True)
    replay.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    config = ExperimentConfig.load(args.config)
    if args.command == "replay":
        report = json.loads(args.report.read_text(encoding="utf-8"))
        count = sum(verify_replay(config, episode["replay"])
                    for episode in report["episodes"])
        print(json.dumps({"verified_episodes": len(report["episodes"]),
                          "verified_decisions": count}))
        return
    if args.command == "evaluate" and args.baseline != "policy":
        result = PolicyEvaluator(config, None).evaluate(args.split, args.baseline)
        write_json(args.output, result)
    else:
        trainer = PpoTrainer(config)
        try:
            if args.command == "train":
                repository = ExperimentRepository(args.run)
                if args.resume:
                    if args.resume.resolve().parent != repository.directory:
                        raise ValueError("Resume must write to the original run directory")
                    trainer.restore(ExperimentRepository.load_checkpoint(args.resume))
                    disk_manifest = json.loads(
                        (repository.directory / "manifest.json").read_text(encoding="utf-8"))
                    if disk_manifest["config_hash"] != config.digest:
                        raise ValueError("Run manifest configuration differs")
                else:
                    if args.steps <= 0:
                        raise ValueError("--steps must be positive")
                    repository.initialize(trainer.manifest())
                checkpoint = trainer.train(args.steps, repository)
                print(f"Checkpoint: {checkpoint}")
                return
            if not args.checkpoint:
                raise ValueError("--checkpoint is required for policy evaluation")
            trainer.restore(ExperimentRepository.load_checkpoint(args.checkpoint))
            trainer.policy.eval()
            result = PolicyEvaluator(config, trainer.policy).evaluate(args.split)
            result["checkpoint"] = str(args.checkpoint.resolve())
            result["checkpoint_sha256"] = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
            result["checkpoint_decisions"] = trainer.decisions
            write_json(args.output, result)
        finally:
            trainer.close()
    print(json.dumps({k: v for k, v in result.items() if k != "episodes"}))


if __name__ == "__main__":
    main()
