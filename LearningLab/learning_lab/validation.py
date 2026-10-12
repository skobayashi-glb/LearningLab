"""Explicit validation policies; individual regressions remain visible as diagnostics."""
from .parameter_space import finite

PRESERVED = 'preserved'
AGGREGATE = 'aggregate'
NO_INCREASE = 0.0


class ValidationPolicy:
    def __init__(self, gate=None):
        self.gate = gate
        self.mode = gate.get('mode', PRESERVED) if gate else PRESERVED
        if gate is None:
            return
        fields = ({'mode', 'max_mean_loss_increase'} if self.mode == AGGREGATE else
                  {'max_case_loss_increase', 'max_character_loss_increase'})
        actual = set(gate) - ({'mode'} if self.mode == PRESERVED else set())
        if self.mode not in (PRESERVED, AGGREGATE) or actual != fields:
            raise ValueError('Unknown validation policy or fields')
        if any(not finite(value) or value < NO_INCREASE for key, value in gate.items() if key != 'mode'):
            raise ValueError('Validation tolerances must be finite and nonnegative')

    def accepts(self, baseline, selected):
        baseline_cases = {case['case']: case for case in baseline['cases']}
        if set(baseline_cases) != {case['case'] for case in selected['cases']}:
            raise ValueError('Validation case coverage changed')
        if self.mode == AGGREGATE:
            return selected['loss'] <= baseline['loss'] + self.gate['max_mean_loss_increase']
        if selected['loss'] > baseline['loss']:
            return False
        tolerance = self.gate['max_case_loss_increase'] if self.gate else NO_INCREASE
        if any(case['loss'] > baseline_cases[case['case']]['loss'] + tolerance for case in selected['cases']):
            return False
        if self.gate:
            for character in {case['character'] for case in baseline['cases']}:
                def average(evaluation):
                    cases = [case for case in evaluation['cases'] if case['character'] == character]
                    return sum(c['loss'] * c['weight'] for c in cases) / sum(c['weight'] for c in cases)
                if average(selected) > average(baseline) + self.gate['max_character_loss_increase']:
                    return False
        return True

    def assess(self, baseline, selected):
        previous = {case['case']: case for case in baseline['cases']}
        accepted = self.accepts(baseline, selected)
        changes = [{'case': case['case'], 'character': case.get('character'),
                    'baseline_loss': previous[case['case']]['loss'], 'selected_loss': case['loss'],
                    'loss_increase': case['loss'] - previous[case['case']]['loss']}
                   for case in selected['cases']]
        return {'mode': self.mode, 'accepted': accepted, 'baseline_loss': baseline['loss'],
                'selected_loss': selected['loss'], 'case_changes': changes}


def preserved_validation(baseline, selected, gate=None):
    return ValidationPolicy(gate).accepts(baseline, selected)
