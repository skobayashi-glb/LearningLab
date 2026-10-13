"""A fork must produce exactly the same proposals and optimizer state as uninterrupted training."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from learning_lab.balance import BalanceSearch
from learning_lab.character_search import PER_CHARACTER
from learning_lab.monitoring import FileSearchMonitor
from test_adaptive_search import adaptive_fixture

PREFIX_BUDGET = 3
TOTAL_BUDGET = 6
TARGET_HP = 75
LOSS_SCALE = 100
VALIDATION_SEED = 41
HOLDOUT_SEED = 51


def search_fixture(budget):
    config, settings = adaptive_fixture()
    settings['candidate_budget'] = budget
    settings['learning_scope'] = PER_CHARACTER
    for case in settings['cases']:
        case['freeze_others'] = True
    return config, settings


def mocked_search(config, settings, calls=None):
    search = BalanceSearch(config, settings)
    def evaluate(candidate, split, *, joint=False):
        if calls is not None:
            calls.append((split, search.learner.updates))
        loss = abs(candidate['fighter']['hp'] - TARGET_HP) / LOSS_SCALE
        return {'split': split, 'loss': loss, 'cases': [
            {'case': case['id'], 'character': case['character'], 'weight': case['weight'], 'loss': loss,
             'episodes': [], 'means': {}} for case in settings['cases']]}
    search.evaluator.evaluate = evaluate
    return search


class SearchContinuationTests(unittest.TestCase):
    def test_fork_matches_uninterrupted_candidates_optimizer_and_keeps_source_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, short_settings = search_fixture(PREFIX_BUDGET)
            prefix = root / 'prefix'
            mocked_search(config, short_settings).run(prefix)
            source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in prefix.iterdir() if path.is_file()}
            _, full_settings = search_fixture(TOTAL_BUDGET)
            full = root / 'full'
            full_report = mocked_search(config, full_settings).run(full)
            fork = root / 'fork'
            monitor = FileSearchMonitor(fork)
            calls = []
            fork_report = mocked_search(config, full_settings, calls).run(fork, observer=monitor, continue_from=prefix)
            self.assertEqual(len([call for call in calls if call[0] == 'train']), TOTAL_BUDGET - PREFIX_BUDGET)
            self.assertEqual([updates for split, updates in calls if split == 'train'],
                             list(range(PREFIX_BUDGET - 1, TOTAL_BUDGET - 1)))
            for index in range(TOTAL_BUDGET):
                name = f'trial-{index:04d}.json'
                self.assertEqual(json.loads((fork / name).read_text()), json.loads((full / name).read_text()))
                if index < PREFIX_BUDGET:
                    self.assertEqual((prefix / name).read_bytes(), (fork / name).read_bytes())
            self.assertEqual(fork_report['final_distribution'], full_report['final_distribution'])
            self.assertEqual(fork_report['selected_train_candidate'], full_report['selected_train_candidate'])
            self.assertEqual(fork_report['continuation']['inherited_updates'], PREFIX_BUDGET - 1)
            self.assertEqual(monitor.snapshot()['completed_trials'], TOTAL_BUDGET)
            self.assertEqual(monitor.snapshot()['updates'], TOTAL_BUDGET - 1)
            self.assertEqual(monitor.snapshot()['inherited_trials'], PREFIX_BUDGET)
            self.assertEqual(source_hashes, {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                           for path in prefix.iterdir() if path.is_file()})

    def test_training_changes_or_nonextending_budget_fail_before_destination_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, settings = search_fixture(PREFIX_BUDGET)
            prefix = root / 'prefix'
            mocked_search(config, settings).run(prefix)
            _, longer = search_fixture(TOTAL_BUDGET)
            alternatives = []
            altered = copy.deepcopy(longer); altered['seeds']['train'] = [11]; alternatives.append(altered)
            altered = copy.deepcopy(longer); altered['change_weight'] *= 2; alternatives.append(altered)
            altered = copy.deepcopy(longer); altered['parameters'][0]['max'] -= 1; alternatives.append(altered)
            altered = copy.deepcopy(longer); altered['candidate_budget'] = PREFIX_BUDGET; alternatives.append(altered)
            for index, altered in enumerate(alternatives):
                output = root / f'invalid-{index}'
                with self.subTest(index=index), self.assertRaises(ValueError):
                    mocked_search(config, altered).run(output, continue_from=prefix)
                self.assertFalse(output.exists())

    def test_new_validation_and_holdout_seeds_do_not_change_proposals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, settings = search_fixture(PREFIX_BUDGET)
            prefix = root / 'prefix'
            mocked_search(config, settings).run(prefix)
            _, settings = search_fixture(TOTAL_BUDGET)
            settings['seeds']['validation'] = [VALIDATION_SEED]
            settings['seeds']['holdout'] = [HOLDOUT_SEED]
            settings['analysis'] = {'enabled': False}
            fork = root / 'fork'
            report = mocked_search(config, settings).run(fork, continue_from=prefix)
            self.assertEqual(report['distribution_updates'], TOTAL_BUDGET - 1)
            self.assertEqual(report['continuation']['inherited_trials'], PREFIX_BUDGET)

    def test_incomplete_trial_history_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, settings = search_fixture(PREFIX_BUDGET)
            prefix = root / 'prefix'
            mocked_search(config, settings).run(prefix)
            (prefix / f'trial-{PREFIX_BUDGET - 1:04d}.json').unlink()
            _, settings = search_fixture(TOTAL_BUDGET)
            output = root / 'fork'
            with self.assertRaises(FileNotFoundError):
                mocked_search(config, settings).run(output, continue_from=prefix)
            self.assertFalse(output.exists())

    def test_unknown_search_implementation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, settings = search_fixture(PREFIX_BUDGET)
            prefix = root / 'prefix'
            mocked_search(config, settings).run(prefix)
            manifest_path = prefix / 'manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['search_code_sha256'] = 'unverified-search'
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            _, settings = search_fixture(TOTAL_BUDGET)
            output = root / 'fork'
            with self.assertRaisesRegex(ValueError, 'compatibility'):
                mocked_search(config, settings).run(output, continue_from=prefix)
            self.assertFalse(output.exists())
