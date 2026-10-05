import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

from check_release_boundary import find_violations
from learning_lab.artifacts import ExperimentRepository
from learning_lab.config import ExperimentConfig, canonical_hash
from learning_lab.demo import NavigationAction, NavigationDemo
from learning_lab.evaluation import PolicyEvaluator, verify_replay
from learning_lab.policy import MaskedActorCritic
from learning_lab.ppo import generalized_advantages
from learning_lab.rpc import JsonProcessEnvironment
from learning_lab.trainer import PpoTrainer

CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "demo.json"
TEST_SEED = 123
TEST_ROLLOUT = 32
TEST_MINIBATCH = 16
TEST_SAMPLE_COUNT = 256
RPC_TIMEOUT_SECONDS = 30


def test_config() -> ExperimentConfig:
    data = ExperimentConfig.load(CONFIG_PATH).data
    data["ppo"]["rollout_steps"] = TEST_ROLLOUT
    data["ppo"]["minibatch_size"] = TEST_MINIBATCH
    return ExperimentConfig(data)


class LearningTests(unittest.TestCase):
    def test_mask_never_samples_illegal_actions(self):
        spec = NavigationDemo().spec
        policy = MaskedActorCritic(spec, TEST_MINIBATCH)
        values = torch.zeros((TEST_SAMPLE_COUNT, spec.observation_size))
        masks = torch.zeros((TEST_SAMPLE_COUNT, spec.action_count), dtype=torch.bool)
        masks[:, NavigationAction.RIGHT] = True
        distribution, _ = policy.distribution_and_value(values, masks)
        self.assertTrue(torch.all(distribution.sample() == NavigationAction.RIGHT))
        with self.assertRaises(ValueError):
            policy.distribution_and_value(values, torch.zeros_like(masks))

    def test_gae_bootstraps_truncation_without_episode_leakage(self):
        rewards = torch.tensor([0.0, 100.0])
        values = torch.tensor([1.0, 0.0])
        next_values = torch.tensor([2.0, 999.0])
        advantages, returns = generalized_advantages(
            rewards, values, next_values,
            torch.tensor([False, True]), torch.tensor([True, False]), 0.9, 1.0)
        self.assertAlmostEqual(advantages[0].item(), 0.8, places=5)
        self.assertAlmostEqual(returns[0].item(), 1.8, places=5)
        self.assertEqual(returns[1].item(), 100.0)

    def test_demo_state_roundtrip_and_terminal_step_rejection(self):
        scenario = test_config().scenarios("train")[0]
        environment = NavigationDemo()
        observation = environment.reset(scenario, TEST_SEED)
        state = environment.save_state()
        direction = (NavigationAction.RIGHT if observation.values[1] > observation.values[0]
                     else NavigationAction.LEFT)
        first = environment.step(direction)
        environment.load_state(state)
        second = environment.step(direction)
        np.testing.assert_array_equal(first.observation.values, second.observation.values)
        while not second.ended:
            second = environment.step(direction)
        self.assertTrue(second.terminated)
        with self.assertRaises(RuntimeError):
            environment.step(NavigationAction.WAIT)

    def test_time_limit_is_not_terminal_or_failure_reward(self):
        environment = NavigationDemo()
        scenario = copy.deepcopy(test_config().scenarios("train")[0])
        scenario["parameters"]["max_steps"] = 1
        environment.reset(scenario, TEST_SEED)
        transition = environment.step(NavigationAction.WAIT)
        self.assertTrue(transition.truncated)
        self.assertFalse(transition.terminated)
        self.assertEqual(transition.reward, 0.0)

    def test_train_sampler_cannot_select_validation(self):
        trainer = PpoTrainer(test_config())
        try:
            expected = {scenario["id"] for scenario in trainer.config.scenarios("train")}
            for _ in range(TEST_SAMPLE_COUNT):
                scenario, _ = trainer.sampler.next()
                self.assertIn(scenario["id"], expected)
        finally:
            trainer.close()

    def test_rejects_split_leakage_and_bad_weights(self):
        data = test_config().data
        data["scenarios"][-1]["family"] = data["scenarios"][0]["family"]
        with self.assertRaises(ValueError):
            ExperimentConfig(data)
        data = test_config().data
        data["scenarios"][0]["weight"] = float("nan")
        with self.assertRaises(ValueError):
            ExperimentConfig(data)

    def test_ppo_updates_weights(self):
        trainer = PpoTrainer(test_config())
        try:
            initial = {key: value.clone() for key, value in trainer.policy.state_dict().items()}
            batch, _ = trainer.collect(TEST_ROLLOUT)
            metrics = trainer.updater.update(batch)
            self.assertTrue(all(np.isfinite(value) for value in metrics.values()))
            self.assertTrue(any(not torch.equal(initial[key], value)
                                for key, value in trainer.policy.state_dict().items()))
        finally:
            trainer.close()

    def test_checkpoint_exact_continuation_and_safe_disk_load(self):
        trainer = PpoTrainer(test_config())
        restored = None
        try:
            batch, _ = trainer.collect(TEST_ROLLOUT)
            trainer.updater.update(batch)
            with tempfile.TemporaryDirectory() as directory:
                repository = ExperimentRepository(Path(directory))
                repository.initialize(trainer.manifest())
                path = repository.save_checkpoint(trainer.checkpoint())
                saved = repository.load_checkpoint(path)
                expected_batch, expected_episodes = trainer.collect(TEST_ROLLOUT)
                trainer.updater.update(expected_batch)
                expected_weights = {k: v.clone() for k, v in trainer.policy.state_dict().items()}
                restored = PpoTrainer(test_config())
                restored.restore(saved)
                actual_batch, actual_episodes = restored.collect(TEST_ROLLOUT)
                restored.updater.update(actual_batch)
                self.assertTrue(torch.equal(expected_batch.actions, actual_batch.actions))
                self.assertEqual(expected_episodes, actual_episodes)
                for name, value in restored.policy.state_dict().items():
                    self.assertTrue(torch.equal(expected_weights[name], value), name)
                changed = test_config().data
                changed["seed"] += 1
                mismatch = PpoTrainer(ExperimentConfig(changed))
                try:
                    with self.assertRaises(ValueError):
                        mismatch.restore(saved)
                finally:
                    mismatch.close()
        finally:
            trainer.close()
            if restored:
                restored.close()

    def test_evaluation_and_replay_detect_tampering(self):
        config = test_config()
        report = PolicyEvaluator(config, None).evaluate("validation", "random")
        self.assertEqual(report["episode_count"], len(config.data["evaluation"]["seeds"]))
        replay = report["episodes"][0]["replay"]
        self.assertEqual(verify_replay(config, replay), len(replay["steps"]))
        replay["steps"][0]["reward"] += 1
        with self.assertRaises(ValueError):
            verify_replay(config, replay)

    def test_json_process_bridge_matches_local_environment(self):
        config = test_config()
        scenario = config.scenarios("train")[0]
        local = NavigationDemo()
        remote = JsonProcessEnvironment(
            [sys.executable, "-m", "learning_lab.demo_server"], RPC_TIMEOUT_SECONDS)
        try:
            expected = local.reset(scenario, TEST_SEED)
            actual = remote.reset(scenario, TEST_SEED)
            np.testing.assert_array_equal(expected.values, actual.values)
            state = remote.save_state()
            for action in (NavigationAction.WAIT, NavigationAction.WAIT):
                expected_step = local.step(action)
                actual_step = remote.step(int(action))
                np.testing.assert_array_equal(expected_step.observation.values,
                                              actual_step.observation.values)
                self.assertEqual(canonical_hash(local.save_state()),
                                 canonical_hash(remote.save_state()))
            np.testing.assert_array_equal(remote.load_state(state).values, actual.values)
        finally:
            remote.close()

    def test_release_guard_rejects_runtime_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            runtime = project / "Assets" / "Runtime"
            runtime.mkdir(parents=True)
            source = runtime / "Unsafe.cs"
            source.write_text("using LearningLab;", encoding="utf-8")
            self.assertTrue(find_violations(project))
            source.write_text("class GameOnly {}", encoding="utf-8")
            self.assertEqual(find_violations(project), [])


if __name__ == "__main__":
    unittest.main()
