"""Measured equal-budget matchups, controlled pair comparisons and roster selection."""
import copy
from itertools import combinations
import random

from .artifacts import write_json
from .config import canonical_hash
from .cost_balance import BudgetArmyBuilder, battle_score, HASH_BASE, reverse_scenario, LEGS

ALLY = 0
WILSON_Z = 1.96
TIE_TOLERANCE = 1e-9


def mean(values):
    return sum(values) / len(values) if values else None


def win_interval(wins, count):
    if not count:
        return None
    fraction = wins / count
    square = WILSON_Z ** 2
    center = (fraction + square / (2 * count)) / (1 + square / count)
    radius = WILSON_Z * ((fraction * (1 - fraction) / count + square / (4 * count ** 2)) ** 0.5) / (1 + square / count)
    return [center - radius, center + radius]


class RosterAnalyzer:
    def __init__(self, config, settings):
        self.config, self.settings = config, settings
        self.options = settings['analysis']
        self.characters = self.options['characters']
        if len(set(self.characters)) != len(self.characters) or not self.characters:
            raise ValueError('Analysis characters must be unique and nonempty')
        self.seeds = self.options['seeds']
        if not self.seeds or len(self.seeds) != len(set(self.seeds)) or any(type(s) is not int or s < 0 for s in self.seeds):
            raise ValueError('Analysis seeds must be unique nonnegative integers')
        used = set(s for seeds in settings['seeds'].values() for s in seeds)
        if used.intersection(self.seeds):
            raise ValueError('Analysis seeds must be separate from train, validation and holdout')
        battle = config.data['scenarios'][0]['parameters']['battle']
        self.costs = {d['id']: d['cost'] for d in battle['ally_definitions']}
        self.pool = sorted(self.costs)
        if set(self.characters) - set(self.pool):
            raise ValueError('Analysis includes unsupported characters')
        self.builder = BudgetArmyBuilder(self.costs, self.options['budget'], battle['rules']['party_size'])
        self.layouts = config.scenarios('validation')  # Same authored layouts, separate analysis seeds.

    def army(self, mandatory, seed):
        rng = random.Random(int(canonical_hash({'mandatory':list(mandatory), 'seed':seed, 'budget':self.builder.budget}), HASH_BASE))
        return self.builder.build(self.pool, rng, mandatory, focused=len(mandatory) == 1)

    def run(self, candidate, observer=None, parameter_version='baseline', include_pairs=True):
        from .balance import apply_candidate
        config = apply_candidate(self.config, candidate)
        scenarios = {s['id']: s for s in config.scenarios('validation')}
        teams = [(c,) for c in self.characters]
        if include_pairs:
            teams += [pair for pair in combinations(self.characters, 2) if sum(self.costs[c] for c in pair) <= self.builder.budget]
        records = []
        total = len(teams) * len(self.characters) * len(self.layouts) * len(self.seeds) * len(LEGS)
        environment = config.create_environment()
        try:
            for team in teams:
                for opponent in self.characters:
                    for layout in self.layouts:
                        for seed in self.seeds:
                            if observer:
                                observer.check()
                            allies, enemies = self.army(team, seed), self.army((opponent,), seed)
                            scenario = copy.deepcopy(scenarios[layout['id']])
                            battle = scenario['parameters']['battle']
                            battle['roster'] = list(dict.fromkeys(allies))
                            battle['rules']['enemy_enabled'] = False
                            battle['opening_wave'] = {'ally':allies,'enemy':enemies}
                            leg_scores, leg_outcomes = [], []
                            for leg in LEGS:
                                current = scenario if leg == LEGS[0] else reverse_scenario(scenario)
                                environment.reset(current, seed)
                                wait = environment.spec.action_names.index('wait')
                                for _ in range(config.data['evaluation']['max_decisions']):
                                    if observer:
                                        observer.check()
                                    transition = environment.step(wait)
                                    if transition.ended:
                                        break
                                else:
                                    raise RuntimeError('Roster analysis watchdog exceeded')
                                debug = environment.save_state()['debug']
                                score = battle_score(debug, transition.info['metrics'], current['parameters']['battle'], target_team=0 if leg == LEGS[0] else 1)['match_score']
                                leg_scores.append(score)
                                leg_outcomes.append(debug["outcome"])
                            records.append({'team':list(team), 'opponent':opponent, 'scenario':layout['id'], 'seed':seed,
                                'ally':allies, 'enemy':enemies, 'ally_cost':sum(self.costs[c] for c in allies),
                                'enemy_cost':sum(self.costs[c] for c in enemies), 'score':mean(leg_scores), 'leg_scores':leg_scores,
                                'leg_outcomes':leg_outcomes, 'wins':int(leg_outcomes[0]=='victory')+int(leg_outcomes[1]=='defeat')})
                            if observer:
                                observer.emit('analysis_progress', completed=len(records)*len(LEGS), total=total, parameter_version=parameter_version)
                if observer:
                    observer.emit('analysis', data=self.summarize(records, total, parameter_version, partial=True))
        finally:
            environment.close()
        result = self.summarize(records, total, parameter_version, partial=False)
        result['scope'] = 'full' if include_pairs else 'focused-only'
        if observer:
            observer.emit('analysis', data=result)
        return result

    def summarize(self, records, total, parameter_version, partial):
        solo = {}
        matchups, strengths, synergy = [], [], []
        for character in self.characters:
            rows = [r for r in records if r['team'] == [character]]
            wins = sum(r['wins'] for r in rows)
            solo[character] = { (r['opponent'],r['scenario'],r['seed']): r['score'] for r in rows }
            strengths.append({'character':character,'cost':self.costs[character], 'score':mean([r['score'] for r in rows]),
                              'trials':len(rows)*len(LEGS), 'wins':wins, 'win_rate':wins/(len(rows)*len(LEGS)) if rows else None,
                              'win_interval':win_interval(wins,len(rows)*len(LEGS))})
            for opponent in self.characters:
                cells = [r for r in rows if r['opponent'] == opponent]
                matchups.append({'character':character,'opponent':opponent,'score':mean([r['score'] for r in cells]),'trials':len(cells)*len(LEGS)})
        for left, right in combinations(self.characters, 2):
            if self.costs[left] + self.costs[right] > self.builder.budget:
                synergy.append({'left':left,'right':right,'delta':None,'trials':0,'reason':'budget exceeded'})
                continue
            deltas = []
            for r in records:
                if r['team'] != [left,right]:
                    continue
                key = (r['opponent'],r['scenario'],r['seed'])
                if key in solo[left] and key in solo[right]:
                    deltas.append(r['score'] - max(solo[left][key],solo[right][key]))
            synergy.append({'left':left,'right':right,'delta':mean(deltas),'trials':len(deltas)*len(LEGS)})
        adoption = {c:0.0 for c in self.characters}
        contexts = {(r['opponent'],r['scenario'],r['seed']) for r in records}
        # Compare only complete candidate sets, including affordable pairs.
        expected = len(self.characters) + sum(self.costs[a]+self.costs[b]<=self.builder.budget for a,b in combinations(self.characters,2))
        complete = 0
        for context in contexts:
            rows = [r for r in records if (r['opponent'],r['scenario'],r['seed']) == context]
            if len(rows) != expected:
                continue
            best = max(r['score'] for r in rows)
            winners = [r for r in rows if abs(r['score']-best) <= TIE_TOLERANCE]
            complete += 1
            for r in winners:
                # Include fillers, because they actually contribute to the selected army.
                for character in set(r['ally']) & set(self.characters):
                    adoption[character] += 1 / len(winners)
        return {'version':1,'parameter_version':parameter_version,'partial':partial,'budget':self.builder.budget,
                'completed':len(records)*len(LEGS),'total':total,'characters':self.characters,'seeds':self.seeds,
                'reversed_legs':True,'config_hash':self.config.digest,'strengths':strengths,'matchups':matchups,'synergy':synergy,
                'adoption':[{'character':c,'rate':adoption[c]/complete if complete else None,'contexts':complete} for c in self.characters],
                'adoption_definition':'Selection among tested focused and pair armies at equal cost; ties split equally. Not player usage.',
                'synergy_definition':'Matched pair army score minus the better focused army score against the same opponent, layout and seed. Fillers are recorded; this is roster advantage, not isolated causal interaction.',
                'records':records}
