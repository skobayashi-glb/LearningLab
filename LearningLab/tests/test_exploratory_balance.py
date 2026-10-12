"""Allow exceptional armies without bypassing combined validation or fair sampling."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from http.server import ThreadingHTTPServer
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

from learning_lab.balance import BalanceSearch
from learning_lab.character_search import PER_CHARACTER
from learning_lab.cost_balance import BudgetWaveEncounter
from learning_lab.validation import ValidationPolicy, preserved_validation
from learning_lab.monitoring import FileSearchMonitor
from learning_lab.dashboard import DashboardService, handler_for
from test_adaptive_search import adaptive_fixture

BUDGET = 30
SEED_START = 14100
SEED_COUNT = 30
BASELINE_LOSS = 1.0
IMPROVED_LOSS = 0.5
REGRESSED_LOSS = 2.0
AGGREGATE_GATE = {'mode': 'aggregate', 'max_mean_loss_increase': 0.0}
HTTP_TIMEOUT = 5
ARCHIVE_STRIDE = 2
SECOND_TRAIN_SEED = 4


class ExploratoryValidationTests(unittest.TestCase):
    def test_aggregate_allows_one_regression_but_rejects_total_regression_and_missing_cases(self):
        baseline = {'loss': BASELINE_LOSS, 'cases': [
            {'case': 'a', 'character': 'a', 'loss': BASELINE_LOSS, 'weight': 1},
            {'case': 'b', 'character': 'b', 'loss': BASELINE_LOSS, 'weight': 1}]}
        selected = copy.deepcopy(baseline)
        selected['loss'] = IMPROVED_LOSS
        selected['cases'][0]['loss'] = REGRESSED_LOSS
        selected['cases'][1]['loss'] = 0.0
        policy = ValidationPolicy(AGGREGATE_GATE)
        self.assertTrue(policy.accepts(baseline, selected))
        self.assertGreater(policy.assess(baseline, selected)['case_changes'][0]['loss_increase'], 0)
        self.assertFalse(preserved_validation(baseline, selected))
        selected['loss'] = REGRESSED_LOSS
        self.assertFalse(policy.accepts(baseline, selected))
        selected['cases'].pop()
        with self.assertRaises(ValueError):
            policy.accepts(baseline, selected)

    def test_bad_gate_configuration_is_rejected(self):
        for gate in ({'mode': 'unknown'}, {'mode': 'aggregate', 'max_mean_loss_increase': -1},
                     {'mode': 'aggregate', 'max_mean_loss_increase': True},
                     {'mode': 'aggregate', 'max_mean_loss_increase': float('nan')},
                     {'mode': 'aggregate', 'max_mean_loss_increase': 0, 'max_case_loss_increase': 1}):
            with self.subTest(gate=gate), self.assertRaises(ValueError):
                ValidationPolicy(gate)

    def run_mock_search(self, joint_loss):
        config, settings = adaptive_fixture()
        settings.update(learning_scope=PER_CHARACTER, always_joint_validation=True,
                        validation_gate=AGGREGATE_GATE, change_weight=0.0)
        for case in settings['cases']:
            case['freeze_others'] = True
        search = BalanceSearch(config, settings)
        calls = []

        def evaluate(candidate, split, *, joint=False):
            baseline = candidate == search.space.baseline
            calls.append((split, joint, baseline))
            loss = BASELINE_LOSS if baseline else IMPROVED_LOSS if split == 'train' else joint_loss if joint else REGRESSED_LOSS
            return {'split': split, 'loss': loss, 'cases': [
                {'case': case['id'], 'character': case['character'], 'loss': loss, 'weight': case['weight']}
                for case in settings['cases']]}

        search.evaluator.evaluate = evaluate
        with tempfile.TemporaryDirectory() as directory:
            report = search.run(Path(directory))
        return report, calls

    def test_joint_runs_even_when_individual_rejected_and_is_decisive(self):
        report, calls = self.run_mock_search(IMPROVED_LOSS)
        self.assertFalse(report['passed_individual_validation'])
        self.assertIn(('validation', True, False), calls)
        self.assertTrue(report['passed_validation_gate'])
        self.assertTrue(report['validation_decision']['accepted'])
        self.assertFalse(report['applied_to_game'])
        report, calls = self.run_mock_search(REGRESSED_LOSS)
        self.assertIn(('validation', True, False), calls)
        self.assertFalse(report['passed_validation_gate'])
        self.assertIn(('holdout', True, True), calls)


class ExploratorySamplingTests(unittest.TestCase):
    def test_balanced_opponents_exact_cost_reproducible_random_order_and_reversed_armies(self):
        config, _ = adaptive_fixture()
        scenario = copy.deepcopy(config.scenarios('train')[0])
        battle = scenario['parameters']['battle']
        definition = copy.deepcopy(battle['ally_definitions'][0])
        definitions = [dict(definition, id=f'unit-{index:02d}', cost=1 + index % 5)
                       for index in range(SEED_COUNT)]
        battle['ally_definitions'] = definitions
        battle['enemy_baseline'] = copy.deepcopy(definitions)
        pool = [unit['id'] for unit in definitions]
        settings = {'pattern': 'mixed', 'budget': BUDGET, 'ally_pool': pool, 'enemy_pool': pool,
                    'opponent_sampling': 'balanced', 'enemy_patterns': ['focused', 'mixed'],
                    'shuffle_spawn_order': True}
        encounter = BudgetWaveEncounter({'character': pool[0], 'encounter': settings})
        opponent_centers, orders, patterns = set(), set(), set()
        for seed in range(SEED_START, SEED_START + SEED_COUNT):
            _, first = encounter.prepare(scenario, seed)
            _, repeated = encounter.prepare(scenario, seed)
            _, reverse = encounter.prepare(scenario, seed, 'reverse')
            self.assertEqual(first, repeated)
            self.assertEqual(first['ally'], reverse['enemy'])
            self.assertEqual(first['enemy'], reverse['ally'])
            self.assertEqual(first['ally_cost'], BUDGET)
            self.assertEqual(first['enemy_cost'], BUDGET)
            opponent_centers.add(first['opponent'])
            orders.add(tuple(first['ally']))
            patterns.add(first['enemy_pattern'])
            other = BudgetWaveEncounter({'character': pool[-1], 'encounter': settings})
            self.assertEqual(first['enemy'], other.prepare(scenario, seed)[1]['enemy'])
        self.assertEqual(opponent_centers, set(pool))
        self.assertGreater(len(orders), 1)
        self.assertEqual(patterns, {'focused', 'mixed'})

    def test_campaign_preserves_exceptional_army_targets_and_joint_policy(self):
        from learning_lab.campaign import build_campaign
        from learning_lab.battle import HOST_DLL
        if not HOST_DLL.exists():
            self.skipTest('Build BattleHost first')
        lab = Path(__file__).resolve().parents[1]
        plan = json.loads((lab / 'configs/exploratory_cost_training_plan.json').read_text(encoding='utf-8'))
        template = json.loads((lab / 'configs/cost_training_stages.json').read_text(encoding='utf-8'))
        _, settings, _ = build_campaign(plan, template)
        self.assertTrue(settings['always_joint_validation'])
        self.assertEqual(settings['validation_gate']['mode'], 'aggregate')
        for case in settings['cases']:
            self.assertEqual(case['encounter']['opponent_sampling'], 'balanced')
            if case['encounter']['pattern'] == 'focused':
                self.assertEqual(case['targets']['match_score']['max'], 1.0)
                self.assertLess(case['weight'], 1.0)
            else:
                self.assertLess(case['targets']['match_score']['max'], 1.0)


class SparseFrameTests(unittest.TestCase):
    def test_streamed_episode_pagination_handles_partial_append(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'episodes.jsonl').write_text('{"id":0}\n{"id":1}\n{"id":2}\n{"id":', encoding='utf-8')
            service = SimpleNamespace(monitor=SimpleNamespace(directory=path))
            self.assertEqual(DashboardService.episodes(service, 1, 1), {'total': 3, 'episodes': [{'id': 1}]})
            with self.assertRaises(ValueError):
                FileSearchMonitor(path, archive_stride=0)

    def test_unsaved_latest_frame_is_served_live_without_affecting_evaluation(self):
        from learning_lab.battle import HOST_DLL
        from learning_lab.balance import BalanceEvaluator, RangeParameterSpace
        from test_monitoring import monitored_settings
        if not HOST_DLL.exists():
            self.skipTest('Build BattleHost first')
        config, settings = monitored_settings()
        settings['seeds']['train'].append(SECOND_TRAIN_SEED)
        candidate = RangeParameterSpace(config, settings).baseline
        ordinary = BalanceEvaluator(config, settings).evaluate(candidate, 'train')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            monitor = FileSearchMonitor(path, archive_stride=ARCHIVE_STRIDE)
            monitor.emit('started', config=config.data, settings=settings, baseline=candidate,
                         axes=[('fighter', 'hp')], bounds=[(10, 100)])
            monitor.emit('trial_started', index=0, candidate=candidate, distribution=None)
            evaluator = BalanceEvaluator(config, settings)
            evaluator.observer = monitor
            self.assertEqual(ordinary, evaluator.evaluate(candidate, 'train'))
            self.assertTrue((path / 'frames/episode-000000.json').exists())
            self.assertFalse((path / 'frames/episode-000001.json').exists())
            self.assertFalse(monitor.snapshot()['recent_episodes'][-1]['frames_archived'])
            expected = monitor.frame_snapshot(1)
            self.assertTrue(expected['frames'])
            service = SimpleNamespace(monitor=monitor)
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(service))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = f'http://127.0.0.1:{server.server_port}/api/frames/1'
            try:
                with urlopen(address, timeout=HTTP_TIMEOUT) as response:
                    self.assertEqual(json.load(response), expected)
                monitor.emit('episode_started', scenario=config.scenarios('train')[0],
                             case=settings['cases'][0], seed=SECOND_TRAIN_SEED, matchup=None)
                self.assertIsNone(monitor.frame_snapshot(1))
                with self.assertRaises(HTTPError) as error:
                    urlopen(address, timeout=HTTP_TIMEOUT)
                self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown()
                thread.join(HTTP_TIMEOUT)
                server.server_close()
