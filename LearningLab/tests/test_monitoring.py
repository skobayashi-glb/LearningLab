import copy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from learning_lab.balance import BalanceEvaluator, BalanceSearch, RangeParameterSpace
from learning_lab.artifacts import REPLACE_ATTEMPTS, write_json
from learning_lab.battle import HOST_DLL
from learning_lab.dashboard import handler_for
from learning_lab.encounters import SINGLE_WAVE
from learning_lab.monitoring import FileSearchMonitor, SearchControl, SearchStopped
from test_adaptive_search import adaptive_fixture

TIMEOUT = 5
CAPTURE_DECISIONS = 10
BUDGET = 2
LOCAL_HOST = "127.0.0.1"
AUTOMATIC_PORT = 0


def monitored_settings():
    config, settings = adaptive_fixture()
    settings['candidate_budget'] = BUDGET
    settings['evaluation_mode'] = SINGLE_WAVE
    settings['cases'][0]['encounter'] = {
        'ally_count': 1, 'enemy_count': 1, 'ally_pool': ['fighter'], 'enemy_pool': ['fighter']}
    return config, settings


class MonitorControlTests(unittest.TestCase):
    def test_transient_windows_lock_retries_without_losing_previous_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'monitor.json'
            write_json(path, {'state': 'previous'})
            actual_replace = os.replace
            calls = []

            def transient(source, destination):
                calls.append(destination)
                if len(calls) == 1:
                    self.assertEqual(json.loads(path.read_text()), {'state': 'previous'})
                    raise PermissionError('temporary scanner lock')
                actual_replace(source, destination)

            with patch('learning_lab.artifacts.os.replace', side_effect=transient), patch('learning_lab.artifacts.time.sleep'):
                write_json(path, {'state': 'current'})
            self.assertEqual(json.loads(path.read_text()), {'state': 'current'})
            with patch('learning_lab.artifacts.os.replace', side_effect=PermissionError('persistent lock')) as replace, patch('learning_lab.artifacts.time.sleep'):
                with self.assertRaises(PermissionError):
                    write_json(path, {'state': 'not committed'})
                self.assertEqual(replace.call_count, REPLACE_ATTEMPTS)
            self.assertEqual(json.loads(path.read_text()), {'state': 'current'})

    def test_pause_resume_and_stop_are_observed_at_safe_checks(self):
        control = SearchControl()
        paused, done = threading.Event(), threading.Event()
        control.request('pause')
        worker = threading.Thread(target=lambda: (control.check(paused.set), done.set()))
        worker.start()
        self.assertTrue(paused.wait(TIMEOUT))
        self.assertFalse(done.is_set())
        control.request('resume')
        self.assertTrue(done.wait(TIMEOUT))
        worker.join(TIMEOUT)
        control.request('stop')
        with self.assertRaises(SearchStopped):
            control.check(paused.set)
        with self.assertRaises(ValueError):
            control.request('unknown')

    def test_sampling_interval_includes_terminal_frame(self):
        with tempfile.TemporaryDirectory() as path:
            monitor = FileSearchMonitor(Path(path), CAPTURE_DECISIONS)
            self.assertTrue(monitor.should_capture(CAPTURE_DECISIONS))
            self.assertFalse(monitor.should_capture(1))
            self.assertTrue(monitor.should_capture(1, ended=True))

    def test_http_reads_frame_and_rejects_traversal_and_foreign_origin(self):
        with tempfile.TemporaryDirectory() as path:
            monitor = FileSearchMonitor(Path(path))
            directory = Path(path) / 'frames'
            directory.mkdir()
            (directory / 'episode-000000.json').write_text('{"frames":[]}', encoding='utf-8')
            service = SimpleNamespace(monitor=monitor, episodes=lambda offset, limit: {'total': 0, 'episodes': []})
            server = ThreadingHTTPServer((LOCAL_HOST, AUTOMATIC_PORT), handler_for(service))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = f'http://{LOCAL_HOST}:{server.server_port}'
            try:
                with urlopen(address + '/api/status', timeout=TIMEOUT) as response:
                    self.assertEqual(json.load(response)['state'], 'starting')
                with urlopen(address + '/api/frames/0', timeout=TIMEOUT) as response:
                    self.assertEqual(json.load(response), {'frames': []})
                request = Request(address + '/api/control', data=b'{"action":"pause"}',
                    headers={'Content-Type': 'application/json', 'Origin': address})
                with urlopen(request, timeout=TIMEOUT) as response:
                    self.assertEqual(json.load(response)['requested'], 'pause')
                request = Request(address + '/api/control', data=b'{"action":"resume"}',
                    headers={'Origin': 'https://example.com'})
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=TIMEOUT)
                self.assertEqual(error.exception.code, 403)
                self.assertTrue(monitor.control.paused)
                with self.assertRaises(HTTPError):
                    urlopen(address + '/api/frames/../manifest', timeout=TIMEOUT)
            finally:
                server.shutdown()
                thread.join(TIMEOUT)
                server.server_close()


@unittest.skipUnless(HOST_DLL.exists(), 'Build BattleHost first')
class MonitoredBattleTests(unittest.TestCase):
    def test_actual_frames_do_not_change_scores_and_show_host_state(self):
        config, settings = monitored_settings()
        candidate = RangeParameterSpace(config, settings).baseline
        ordinary = BalanceEvaluator(config, settings).evaluate(candidate, 'train')
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            monitor = FileSearchMonitor(directory)
            monitor.emit('started', config=config.data, settings=settings, baseline=candidate,
                axes=[('fighter', 'hp')], bounds=[(10, 100)])
            monitor.emit('trial_started', index=0, candidate=candidate, distribution=None)
            evaluator = BalanceEvaluator(config, settings)
            evaluator.observer = monitor
            observed = evaluator.evaluate(candidate, 'train')
            self.assertEqual(ordinary, observed)
            episode = json.loads((directory / 'frames/episode-000000.json').read_text())
            frames = episode['frames']
            self.assertEqual(frames[0]['tick'], 0)
            self.assertGreater(frames[-1]['tick'], frames[0]['tick'])
            self.assertEqual(len(frames[0]['units']), 2)
            self.assertTrue(all(unit['character'] == 'fighter' for unit in frames[0]['units']))
            self.assertTrue(any(unit['spawned'] for frame in frames for unit in frame['units']))
            self.assertEqual(episode['candidate'], candidate)
            self.assertEqual(monitor.snapshot()['phase_completed'], 1)

    def test_candidate_updates_and_report_are_identical_with_monitoring(self):
        config, settings = monitored_settings()
        with tempfile.TemporaryDirectory() as root:
            plain = BalanceSearch(config, settings).run(Path(root) / 'plain')
            directory = Path(root) / 'observed'
            monitor = FileSearchMonitor(directory)
            observed = BalanceSearch(config, settings).run(directory, observer=monitor)
            self.assertEqual(plain, observed)
            status = monitor.snapshot()
            self.assertEqual(status['state'], 'completed')
            self.assertEqual(status['updates'], BUDGET - 1)
            self.assertEqual(len(status['history']), BUDGET)
            self.assertTrue((directory / 'checkpoint.pt').exists())


if __name__ == '__main__':
    unittest.main()
