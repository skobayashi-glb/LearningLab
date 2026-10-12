"""Authored costs, exact-budget armies and comparable combat scores."""
import copy
import random

from .config import canonical_hash

BUDGET_WAVE = "budget-wave"
MINIMUM_COST = 1
MAXIMUM_COST = 20
DEFAULT_BUDGET = 30
PARTY_CAPACITY = 12
HASH_BASE = 16
MIDPOINT = 0.5
TIMEOUT_BASE_WEIGHT = 0.25
TIMEOUT_SURVIVAL_WEIGHT = 0.25
LEGS = ('forward', 'reverse')


def reverse_scenario(scenario):
    result = copy.deepcopy(scenario)
    battle = result['parameters']['battle']
    battle['ally_definitions'], battle['enemy_baseline'] = battle['enemy_baseline'], battle['ally_definitions']
    wave = battle['opening_wave']
    wave['ally'], wave['enemy'] = wave['enemy'], wave['ally']
    battle['roster'] = list(dict.fromkeys(wave['ally']))
    return result


def validate_costs(costs, characters):
    if set(costs) != set(characters):
        raise ValueError("Authored costs must cover every character exactly")
    if any(type(v) is not int or not MINIMUM_COST <= v <= MAXIMUM_COST for v in costs.values()):
        raise ValueError("Authored character costs must be integers from 1 to 20")


class BudgetArmyBuilder:
    def __init__(self, costs, budget=DEFAULT_BUDGET, capacity=PARTY_CAPACITY):
        if type(budget) is not int or budget < MINIMUM_COST:
            raise ValueError("Army budget must be a positive integer")
        if not costs or any(type(v) is not int or not MINIMUM_COST <= v <= MAXIMUM_COST for v in costs.values()):
            raise ValueError("Army costs must be authored integers from 1 to 20")
        self.costs, self.budget, self.capacity = dict(costs), budget, capacity

    def can_fill(self, amount, pool):
        reachable = {0}
        for value in range(amount + 1):
            if value in reachable:
                reachable.update(value + self.costs[c] for c in pool if value + self.costs[c] <= amount)
        return amount in reachable

    def build(self, pool, rng, mandatory=(), focused=False):
        pool = sorted(set(pool))
        if not pool or set(pool) - self.costs.keys() or set(mandatory) - set(pool):
            raise ValueError("Budget army contains an undefined character")
        army = list(mandatory)
        remaining = self.budget - sum(self.costs[c] for c in army)
        if remaining < 0 or len(set(army)) > self.capacity:
            raise ValueError("Required characters exceed the budget or party capacity")
        if focused and len(mandatory) == 1:
            target = mandatory[0]
            while self.costs[target] <= remaining:
                after = remaining - self.costs[target]
                if not self.can_fill(after, pool):
                    break
                army.append(target)
                remaining = after
        while remaining:
            options = []
            for character in pool:
                if self.costs[character] > remaining:
                    continue
                unique = set(army) | {character}
                if len(unique) > self.capacity:
                    continue
                future = pool if len(unique) < self.capacity else sorted(unique)
                if self.can_fill(remaining - self.costs[character], future):
                    options.append(character)
            if not options:
                raise ValueError("Cannot spend the exact army budget; revise costs or pool")
            character = rng.choice(options)
            army.append(character)
            remaining -= self.costs[character]
        return army


class BudgetWaveEncounter:
    def __init__(self, case):
        self.case, self.settings = case, case['encounter']
        if self.settings.get('pattern') not in ('focused', 'mixed'):
            raise ValueError("Budget pattern must be focused or mixed")
        for side in ('ally', 'enemy'):
            pool = self.settings[side + '_pool']
            if not isinstance(pool, list) or not pool or len(pool) != len(set(pool)):
                raise ValueError("Budget encounter pools must be unique and nonempty")
        if case['character'] not in self.settings['ally_pool']:
            raise ValueError("Budget case must expose its target")

    def prepare(self, scenario, seed, leg='forward'):
        if leg not in LEGS:
            raise ValueError('Unknown comparison leg')
        result = copy.deepcopy(scenario)
        battle = result['parameters']['battle']
        costs = {d['id']: d['cost'] for d in battle['ally_definitions']}
        builder = BudgetArmyBuilder(costs, self.settings['budget'], battle['rules'].get('party_size', PARTY_CAPACITY))
        # Opponent schedule never depends on target ID, layout or candidate values.
        shared = {'seed': seed, 'pattern': self.settings['pattern'], 'budget': builder.budget}
        enemy_rng = random.Random(int(canonical_hash(shared), HASH_BASE))
        enemy_target = enemy_rng.choice(sorted(self.settings['enemy_pool']))
        enemies = builder.build(self.settings['enemy_pool'], enemy_rng, [enemy_target], focused=True)
        ally_rng = random.Random(int(canonical_hash(shared | {'target': self.case['character']}), HASH_BASE))
        allies = builder.build(self.settings['ally_pool'], ally_rng, [self.case['character']],
                               focused=self.settings['pattern'] == 'focused')
        battle['roster'] = list(dict.fromkeys(allies))
        battle['rules']['enemy_enabled'] = False
        battle['opening_wave'] = {'ally': allies, 'enemy': enemies}
        metadata = {'seed': seed, 'ally': allies, 'enemy': enemies, 'budget': builder.budget,
                        'ally_cost': sum(costs[c] for c in allies), 'enemy_cost': sum(costs[c] for c in enemies),
                        'opponent': enemy_target, 'pattern': self.settings['pattern'], 'leg':leg, 'target_team':0}
        if leg == 'reverse':
            result = reverse_scenario(result)
            metadata['ally'], metadata['enemy'] = enemies, allies
            metadata['target_team'] = 1
        return result, metadata


def battle_score(debug, metrics, battle, target_team=0):
    bases = battle['stage']['bases']
    ally_index = next(i for i, b in enumerate(bases) if b['main'])
    enemy_index = next(i for i, b in enumerate(bases) if b['boss'])
    ally_hp = max(0, debug['bases'][ally_index]['hp']) / bases[ally_index]['hp']
    enemy_hp = max(0, debug['bases'][enemy_index]['hp']) / bases[enemy_index]['hp']
    costs = {d['id']: d['cost'] for d in battle['ally_definitions']}
    survivors = []
    for team in (0, 1):
        rows = [m for m in metrics if m['team'] == team]
        spent = sum(m['deployed'] * costs[m['character']] for m in rows)
        alive = sum((m['deployed'] - m['deaths']) * costs[m['character']] for m in rows)
        survivors.append(alive / spent if spent else 0)
    outcome = debug['outcome']
    value = 1.0 if outcome == 'victory' else 0.0 if outcome == 'defeat' else (
        MIDPOINT + TIMEOUT_BASE_WEIGHT * (ally_hp - enemy_hp) +
        TIMEOUT_SURVIVAL_WEIGHT * (survivors[0] - survivors[1]))
    return {'match_score': value if target_team == 0 else 1-value,
            'enemy_hp_ratio': enemy_hp if target_team == 0 else ally_hp, 'team_survival_fraction': survivors[target_team]}
