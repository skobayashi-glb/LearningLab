"""Independent proposal optimizers receive only their own character's feedback."""
import copy

from .adaptive_search import AdaptiveProposalLearner
from .config import canonical_hash
from .parameter_space import RangeParameterSpace

PER_CHARACTER = 'per-character'
HASH_BASE = 16
SEED_MODULUS = 2 ** 63 - 1
STATE_VERSION = 1


class CharacterProposalLearner:
    def __init__(self, config, settings, space):
        self.space = space
        self.learners = {}
        for character in dict.fromkeys(c for c, _ in space.axes):
            subset = copy.deepcopy(settings)
            subset['parameters'] = [p for p in settings['parameters'] if p['character'] == character]
            subspace = RangeParameterSpace(config, subset)
            seed = int(canonical_hash({'seed': settings['search_seed'], 'character': character}), HASH_BASE) % SEED_MODULUS
            self.learners[character] = AdaptiveProposalLearner(subspace, settings.get('adaptive', {}), seed)
        self.options = settings.get('adaptive', {})

    @property
    def updates(self):
        return min(learner.updates for learner in self.learners.values())

    def initialize_score(self, scores):
        self._validate_scores(scores)
        for character, learner in self.learners.items():
            learner.initialize_score(scores[character])

    def _validate_scores(self, scores):
        if set(scores) != set(self.learners):
            raise ValueError('Feedback must cover all adjusted characters exactly')

    def ask(self):
        return {character: learner.ask()[character] for character, learner in self.learners.items()}

    def tell(self, candidate, scores):
        self._validate_scores(scores)
        updates = {character: learner.tell({character: candidate[character]}, scores[character])
                   for character, learner in self.learners.items()}
        count = len(updates)
        return {'update': self.updates, 'per_character': updates,
                'advantage': sum(u['advantage'] for u in updates.values()) / count,
                'gradient_norm': sum(u['gradient_norm'] for u in updates.values()) / count}

    def scores(self, evaluation, candidate, change_weight):
        scores = {}
        for character, learner in self.learners.items():
            cases = [c for c in evaluation['cases'] if c.get('character') == character]
            if not cases:
                raise ValueError('No measured cases for ' + character)
            loss = sum(c['loss'] * c['weight'] for c in cases) / sum(c['weight'] for c in cases)
            scores[character] = loss + change_weight * learner.space.change_penalty({character: candidate[character]})
        return scores

    def describe(self):
        descriptions = {c: learner.describe() for c, learner in self.learners.items()}
        positions = {axis: (c, i) for c, learner in self.learners.items() for i, axis in enumerate(learner.space.axes)}
        result = {'updates': self.updates, 'per_character': descriptions,
                  'reference_score': sum(d['reference_score'] or 0 for d in descriptions.values()) / len(descriptions),
                  'center_candidate': {c: d['center_candidate'][c] for c, d in descriptions.items()}}
        for field in ('latent_mean', 'latent_std'):
            result[field] = [descriptions[positions[axis][0]][field][positions[axis][1]] for axis in self.space.axes]
        return result

    def state_dict(self):
        return {'version': STATE_VERSION, 'strategy': PER_CHARACTER,
                'updates': self.updates, 'characters': {c: l.state_dict() for c, l in self.learners.items()}}

    def load_state_dict(self, state):
        if state['version'] != STATE_VERSION or state['strategy'] != PER_CHARACTER or set(state['characters']) != set(self.learners):
            raise ValueError('Character checkpoint does not match this cohort')
        for character, learner in self.learners.items():
            learner.load_state_dict(state['characters'][character])
