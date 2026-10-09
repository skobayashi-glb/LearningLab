"""Feedback direction, bounded exploration, exact continuation and split isolation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import torch

from learning_lab.adaptive_search import AdaptiveProposalLearner, ADAPTIVE_STRATEGY
from learning_lab.artifacts import ExperimentRepository
from learning_lab.balance import BalanceSearch
from learning_lab.parameter_space import RangeParameterSpace
from test_balance import fixture

LOW, HIGH = 10, 100
BASELINE_HP, TARGET_HP = 20, 75
SEED = 12
BUDGET = 6
OPTIMIZATION_STEPS = 256
REFERENCE_SCORE = 10.0
GOOD_SCORE, BAD_SCORE = 0.0, 20.0
FRACTION = 0.25
DECODED_HP = 32
TORCH_RTOL, TORCH_ATOL = 0, 0


def adaptive_fixture():
    config, settings = fixture()
    settings['search_strategy'] = ADAPTIVE_STRATEGY
    settings['parameters'] = [{'character': 'fighter', 'field': 'hp', 'min': LOW, 'max': HIGH}]
    settings['candidate_budget'] = BUDGET
    return config, settings


class AdaptiveLearnerTests(unittest.TestCase):
    def setUp(self):
        self.config, self.settings = adaptive_fixture()
        self.space = RangeParameterSpace(self.config, self.settings)

    def learner(self):
        learner = AdaptiveProposalLearner(self.space, {'entropy_coefficient': 0}, SEED)
        learner.initialize_score(REFERENCE_SCORE)
        return learner

    def test_full_interval_and_invalid_domains(self):
        self.assertEqual(self.space.decode([FRACTION]), {'fighter': {'hp': DECODED_HP}})
        self.assertEqual(self.space.decode([0]), {'fighter': {'hp': LOW}})
        self.assertEqual(self.space.decode([1]), {'fighter': {'hp': HIGH}})
        for change in ({'min': 0}, {'min': HIGH}, {'max': float('inf')},
                       {'values': [LOW, HIGH]}, {'field': 'invalid'}, {'min': LOW + FRACTION}):
            settings = copy.deepcopy(self.settings)
            settings['parameters'][0].update(change)
            with self.assertRaises(ValueError):
                RangeParameterSpace(self.config, settings)

    def test_good_feedback_moves_mean_toward_sample_and_bad_feedback_away(self):
        good, bad = self.learner(), self.learner()
        before = good.mean.detach().clone()
        good_candidate, bad_candidate = good.ask(), bad.ask()
        self.assertEqual(good_candidate, bad_candidate)
        offset = good.pending['latent'] - before
        good.tell(good_candidate, GOOD_SCORE)
        bad.tell(bad_candidate, BAD_SCORE)
        self.assertTrue(torch.all((good.mean.detach() - before) * offset > 0))
        self.assertTrue(torch.all((bad.mean.detach() - before) * offset < 0))

    def test_proposal_requires_exactly_one_feedback_and_valid_score(self):
        learner = self.learner()
        candidate = learner.ask()
        with self.assertRaises(RuntimeError):
            learner.ask()
        with self.assertRaises(RuntimeError):
            learner.state_dict()
        with self.assertRaises(ValueError):
            learner.tell(candidate, float('nan'))
        learner.tell(candidate, GOOD_SCORE)
        with self.assertRaises(ValueError):
            learner.tell(candidate, GOOD_SCORE)
        self.assertIsNotNone(learner.ask())

    def test_saved_optimizer_and_rng_continue_identically(self):
        learner = self.learner()
        learner.tell(learner.ask(), GOOD_SCORE)
        restored = self.learner()
        with tempfile.TemporaryDirectory() as directory:
            repository = ExperimentRepository(Path(directory))
            repository.save_checkpoint({'version': 1, 'learner': learner.state_dict()})
            restored.load_state_dict(repository.load_checkpoint(Path(directory) / 'checkpoint.pt')['learner'])
        candidate, replay = learner.ask(), restored.ask()
        self.assertEqual(candidate, replay)
        self.assertEqual(learner.tell(candidate, BAD_SCORE), restored.tell(replay, BAD_SCORE))
        torch.testing.assert_close(learner.mean, restored.mean, rtol=TORCH_RTOL, atol=TORCH_ATOL)
        torch.testing.assert_close(learner.log_std, restored.log_std, rtol=TORCH_RTOL, atol=TORCH_ATOL)

    def test_synthetic_cost_learns_beyond_original_three_values(self):
        learner = AdaptiveProposalLearner(self.space, {}, SEED)
        learner.initialize_score(((BASELINE_HP - TARGET_HP) / HIGH) ** 2)
        best = abs(BASELINE_HP - TARGET_HP)
        observed = set()
        initial_center = learner.describe()['center_candidate']['fighter']['hp']
        for _ in range(OPTIMIZATION_STEPS):
            candidate = learner.ask()
            hp = candidate['fighter']['hp']
            observed.add(hp)
            best = min(best, abs(hp - TARGET_HP))
            learner.tell(candidate, ((hp - TARGET_HP) / HIGH) ** 2)
        center = learner.describe()['center_candidate']['fighter']['hp']
        self.assertLess(abs(center - TARGET_HP), abs(initial_center - TARGET_HP))
        self.assertEqual(best, 0)
        self.assertGreater(len(observed), len({LOW, BASELINE_HP, HIGH}))


class AdaptiveSearchTests(unittest.TestCase):
    def test_updates_before_next_proposal_and_never_uses_validation_feedback(self):
        config, settings = adaptive_fixture()
        search = BalanceSearch(config, settings)
        calls = []

        def evaluate(candidate, split):
            calls.append((split, search.learner.updates))
            # Make every changed candidate improve train but fail validation.
            baseline = candidate == search.space.baseline
            loss = REFERENCE_SCORE if baseline else GOOD_SCORE if split == 'train' else BAD_SCORE
            return {'split': split, 'loss': loss, 'cases': []}

        search.evaluator.evaluate = evaluate
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            report = search.run(path)
            trials = [json.loads(p.read_text()) for p in sorted(path.glob('trial-*.json'))]
            checkpoint = ExperimentRepository.load_checkpoint(path / 'checkpoint.pt')
        expected_updates = BUDGET - 1
        self.assertEqual([update for split, update in calls if split == 'train'], [0, *range(expected_updates)])
        self.assertTrue(all(update == expected_updates for split, update in calls if split != 'train'))
        self.assertIsNone(trials[0]['update'])
        self.assertEqual([t['update']['update'] for t in trials[1:]], list(range(1, BUDGET)))
        self.assertEqual(checkpoint['learner']['updates'], expected_updates)
        self.assertFalse(report['passed_validation_gate'])
        self.assertEqual(report['recommended_overrides'], search.space.baseline)
        self.assertFalse(report['applied_to_game'])

    def test_unknown_strategy_is_rejected(self):
        config, settings = adaptive_fixture()
        settings['search_strategy'] = 'unknown'
        with self.assertRaises(ValueError):
            BalanceSearch(config, settings)
