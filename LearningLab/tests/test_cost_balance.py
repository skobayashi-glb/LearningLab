import copy
import json
from pathlib import Path
import random
import tempfile
import unittest

import torch

from learning_lab.cost_balance import BudgetArmyBuilder, BudgetWaveEncounter, BUDGET_WAVE, validate_costs
from learning_lab.character_search import CharacterProposalLearner
from learning_lab.parameter_space import RangeParameterSpace
from learning_lab.roster_analysis import RosterAnalyzer
from learning_lab.balance import BalanceSearch
from learning_lab.campaign import apply_strength_assignments
from learning_lab.battle import HOST_DLL
from learning_lab.validation import preserved_validation
from test_adaptive_search import adaptive_fixture

LAB = Path(__file__).resolve().parents[1]
BUDGET = 30
CHEAP, HIGH = 1, 20
SEED = 11
ITERATIONS = 100


class BudgetTests(unittest.TestCase):
    def test_exact_budget_and_many_cheap_units_with_unique_party_limit(self):
        builder = BudgetArmyBuilder({'cheap':CHEAP, 'high':HIGH})
        self.assertEqual(len(builder.build(['cheap'],random.Random(SEED),['cheap'],focused=True)),BUDGET)
        for seed in range(ITERATIONS):
            army = builder.build(['cheap','high'],random.Random(seed),['high'])
            self.assertEqual(sum(builder.costs[c] for c in army),BUDGET)
            self.assertIn('high',army)
        with self.assertRaises(ValueError):
            BudgetArmyBuilder({'bad':HIGH+1})
        with self.assertRaises(ValueError):
            BudgetArmyBuilder({'bad':4},budget=5).build(['bad'],random.Random(SEED),['bad'])

    def test_authored_costs_are_validated_and_do_not_mutate_strengths(self):
        plan={'profiles':[{'id':'a','cost':1}], 'strength_bands':{'weak':[2,4]}}
        strengths={'version':1,'assignments':{'a':'weak'},'costs':{'a':HIGH}}
        changed=apply_strength_assignments(plan,strengths)
        self.assertEqual(changed['profiles'][0]['cost'],HIGH)
        self.assertEqual(plan['profiles'][0]['cost'],CHEAP)
        for costs in ({'a':0},{'a':HIGH+1},{'b':CHEAP},{'a':True}):
            with self.assertRaises(ValueError): validate_costs(costs,['a'])

    def test_opponent_is_common_across_targets_layouts_and_candidate_stats(self):
        config,_=adaptive_fixture()
        scenario=copy.deepcopy(config.scenarios('train')[0])
        battle=scenario['parameters']['battle']
        unit=copy.deepcopy(battle['ally_definitions'][0]);unit['id']='second';unit['cost']=HIGH
        battle['ally_definitions'].append(unit)
        battle['enemy_baseline']=copy.deepcopy(battle['ally_definitions'])
        pool=[d['id'] for d in battle['ally_definitions']]
        def prepare(character,current):
            case={'character':character,'encounter':{'pattern':'mixed','budget':BUDGET,'ally_pool':pool,'enemy_pool':pool}}
            return BudgetWaveEncounter(case).prepare(current,SEED)[1]
        first=prepare('fighter',scenario)
        scenario['parameters']['battle']['ally_definitions'][0]['hp']+=1
        second=prepare('second',scenario)
        self.assertEqual(first['enemy'],second['enemy'])
        self.assertEqual(first['ally_cost'],BUDGET)
        self.assertEqual(second['enemy_cost'],BUDGET)


class CharacterLearnerTests(unittest.TestCase):
    def test_validation_rejects_character_regression_even_if_total_improves(self):
        baseline={'loss':1,'cases':[{'case':'a','character':'a','loss':1,'weight':1},{'case':'b','character':'b','loss':1,'weight':1}]}
        selected={'loss':0.8,'cases':[{'case':'a','character':'a','loss':1.02,'weight':1},{'case':'b','character':'b','loss':0.58,'weight':1}]}
        gate={'max_case_loss_increase':0.05,'max_character_loss_increase':0.01}
        self.assertFalse(preserved_validation(baseline,selected,gate))
        selected['cases'][0]['loss']=1.005
        self.assertTrue(preserved_validation(baseline,selected,gate))

    def test_feedback_is_independent_and_checkpoint_continues_exactly(self):
        config,settings=adaptive_fixture()
        for scenario in config.data['scenarios']:
            unit=copy.deepcopy(scenario['parameters']['battle']['ally_definitions'][0]);unit['id']='second'
            scenario['parameters']['battle']['ally_definitions'].append(unit)
        settings['parameters'].append(dict(settings['parameters'][0],character='second'))
        space=RangeParameterSpace(config,settings)
        first,second=(CharacterProposalLearner(config,settings,space) for _ in range(2))
        for learner in (first,second): learner.initialize_score({'fighter':1.0,'second':1.0})
        candidate=first.ask();self.assertEqual(candidate,second.ask())
        first.tell(candidate,{'fighter':0.0,'second':2.0})
        second.tell(candidate,{'fighter':2.0,'second':2.0})
        torch.testing.assert_close(first.learners['second'].mean,second.learners['second'].mean)
        self.assertFalse(torch.equal(first.learners['fighter'].mean,second.learners['fighter'].mean))
        restored=CharacterProposalLearner(config,settings,space);restored.load_state_dict(first.state_dict())
        self.assertEqual(first.ask(),restored.ask())


@unittest.skipUnless(HOST_DLL.exists(),'Build BattleHost first')
class BudgetHostTests(unittest.TestCase):
    def test_real_budget_campaign_and_observed_fixed_cost_search(self):
        campaign=LAB/'runs/cost-campaign-20261012'
        if not campaign.exists(): self.skipTest('Generate cost campaign for integration checks')
        from learning_lab.config import ExperimentConfig
        config=ExperimentConfig.load(campaign/'experiment.json')
        settings=json.loads((campaign/'targets.json').read_text(encoding='utf-8'))
        settings['cases']=[c for c in settings['cases'] if c['character']=='shark']
        settings['parameters']=[p for p in settings['parameters'] if p['character']=='shark']
        settings['candidate_budget']=2;settings['analysis']['enabled']=False
        settings['seeds']={split:seeds[:1] for split,seeds in settings['seeds'].items()}
        search=BalanceSearch(config,settings)
        with tempfile.TemporaryDirectory() as directory:
            report=search.run(Path(directory))
            trial=json.loads((Path(directory)/'trial-0001.json').read_text(encoding='utf-8'))
        self.assertEqual(report['distribution_updates'],1)
        self.assertEqual(set(trial['character_scores']),{'shark'})
        for case in trial['evaluation']['cases']:
            for episode in case['episodes']:
                self.assertEqual(episode['encounter']['ally_cost'],BUDGET)
                self.assertEqual(episode['encounter']['enemy_cost'],BUDGET)
                self.assertTrue(0<=episode['metrics']['match_score']<=1)
        reference_cost=next(c['reference_cost'] for c in settings['cases'] if c['character']=='shark')
        self.assertEqual(report['authored_costs']['shark'],reference_cost)

    def test_analysis_is_measured_and_separate_from_learning_seeds(self):
        campaign=LAB/'runs/cost-campaign-20261012'
        if not campaign.exists(): self.skipTest('Generate cost campaign first')
        from learning_lab.config import ExperimentConfig
        config=ExperimentConfig.load(campaign/'experiment.json')
        settings=json.loads((campaign/'targets.json').read_text(encoding='utf-8'))
        settings['analysis'].update(characters=['shark'],seeds=[13101])
        analyzer=RosterAnalyzer(config,settings)
        result=analyzer.run({})
        self.assertFalse(result['partial'])
        self.assertEqual(result['completed'],4)
        self.assertTrue(all(r['ally_cost']==BUDGET==r['enemy_cost'] for r in result['records']))
        self.assertEqual(result['adoption'][0]['rate'],1)
        self.assertEqual(result['matchups'][0]['score'],0.5)
        settings['analysis']['seeds']=settings['seeds']['train']
        with self.assertRaises(ValueError): RosterAnalyzer(config,settings)
