"""One explicit acceptance rule for isolated and combined character candidates."""


def preserved_validation(baseline, selected, gate=None):
    if selected['loss'] > baseline['loss']:
        return False
    baseline_cases = {case['case']: case for case in baseline['cases']}
    if set(baseline_cases) != {case['case'] for case in selected['cases']}:
        raise ValueError('Validation case coverage changed')
    tolerance = gate['max_case_loss_increase'] if gate else 0
    if any(case['loss'] > baseline_cases[case['case']]['loss'] + tolerance for case in selected['cases']):
        return False
    if gate:
        for character in {case['character'] for case in baseline['cases']}:
            def average(evaluation):
                cases = [case for case in evaluation['cases'] if case['character'] == character]
                return sum(c['loss'] * c['weight'] for c in cases) / sum(c['weight'] for c in cases)
            if average(selected) > average(baseline) + gate['max_character_loss_increase']:
                return False
    return True
