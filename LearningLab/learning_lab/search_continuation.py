"""Fork a fresh search from an exact completed proposal checkpoint and measured history."""
import copy
import hashlib
import json
from pathlib import Path

from .artifacts import ExperimentRepository
from .config import canonical_hash

MUTABLE_SETTINGS = {'candidate_budget', 'character_plan_hash', 'analysis'}
TRAIN_SPLIT = 'train'
FIRST_TRIAL = 0
TRIAL_INCREMENT = 1
LEGACY_SEARCH_HASHES = {'a85473b335ac24f5dc40902100739060ac4c7d79f9389bf79ee3f51204fd81ea'}
COMPATIBLE_CODE = {
    'adaptive_code_sha256': 'adaptive_search.py',
    'parameter_space_code_sha256': 'parameter_space.py',
    'character_search_code_sha256': 'character_search.py',
    'cost_balance_code_sha256': 'cost_balance.py',
    'encounter_code_sha256': 'encounters.py',
}


class SearchContinuation:
    def __init__(self, source, config, settings, learner):
        self.source = Path(source).resolve()
        if learner is None:
            raise ValueError('Continuation requires an adaptive proposal learner')
        self.manifest = self.read('manifest.json')
        current_search = hashlib.sha256(Path(__file__).with_name('balance.py').read_bytes()).hexdigest()
        if self.manifest.get('search_code_sha256') not in LEGACY_SEARCH_HASHES | {current_search}:
            raise ValueError('Source search implementation has no verified continuation compatibility')
        checkpoint_path = self.source / 'checkpoint.pt'
        checkpoint = ExperimentRepository.load_checkpoint(checkpoint_path)
        if self.manifest['config_hash'] != config.digest or checkpoint['config_hash'] != config.digest:
            raise ValueError('Continuation must preserve the frozen battle configuration')
        if canonical_hash(self.manifest['config']) != config.digest:
            raise ValueError('Source frozen configuration does not match its digest')
        previous = self.manifest['settings']
        if checkpoint['settings_hash'] != canonical_hash(previous):
            raise ValueError('Source checkpoint settings do not match its manifest')
        if self.training_settings(previous) != self.training_settings(settings):
            raise ValueError('Continuation cannot change train seeds, objectives, bounds or optimizer settings')
        for key, filename in COMPATIBLE_CODE.items():
            current = hashlib.sha256(Path(__file__).with_name(filename).read_bytes()).hexdigest()
            if self.manifest.get(key) != current:
                raise ValueError(f'Continuation numerical code changed: {filename}')
        last_trial = checkpoint['trial']
        if type(last_trial) is not int or last_trial < FIRST_TRIAL:
            raise ValueError('Source checkpoint has an invalid trial index')
        self.trials = []
        self.trial_hashes = {}
        for index in range(last_trial + TRIAL_INCREMENT):
            name = f'trial-{index:04d}.json'
            trial = self.read(name)
            if trial['index'] != index or trial['candidate_hash'] != canonical_hash(trial['candidate']):
                raise ValueError('Source trial history does not match its recorded candidate')
            if trial.get('proposal_after', {}).get('updates') != index:
                raise ValueError('Source trial and proposal update count disagree')
            self.trials.append(trial)
            self.trial_hashes[name] = hashlib.sha256((self.source / name).read_bytes()).hexdigest()
        if settings['candidate_budget'] <= len(self.trials):
            raise ValueError('Continuation budget must exceed the inherited trial count')
        learner.load_state_dict(checkpoint['learner'])
        if learner.updates != last_trial:
            raise ValueError('Source checkpoint and completed trial count disagree')
        self.metadata = {'source_run': str(self.source), 'inherited_trials': len(self.trials),
                         'inherited_updates': learner.updates, 'source_config_hash': config.digest,
                         'source_settings_hash': checkpoint['settings_hash'],
                         'source_manifest_sha256': hashlib.sha256((self.source / 'manifest.json').read_bytes()).hexdigest(),
                         'source_checkpoint_sha256': hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
                         'source_trial_sha256': self.trial_hashes,
                         'evaluation_history_reused': True, 'train_conditions_unchanged': True}

    def read(self, name):
        return json.loads((self.source / name).read_text(encoding='utf-8'))

    @staticmethod
    def training_settings(settings):
        result = copy.deepcopy(settings)
        for key in MUTABLE_SETTINGS:
            result.pop(key, None)
        result['seeds'] = {TRAIN_SPLIT: result['seeds'][TRAIN_SPLIT]}
        return result

    def copy_trials(self, directory):
        directory = Path(directory)
        for name, expected in self.trial_hashes.items():
            data = (self.source / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('Source history changed during continuation setup')
            (directory / name).write_bytes(data)
