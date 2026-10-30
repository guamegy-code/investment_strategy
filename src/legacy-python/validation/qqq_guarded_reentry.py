"""Guard-preserving reentry, closed-loop branches and a small action selector."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PROJECT_ROOT, DATA_DIR, EXTENDED_DATA_DIR
from pension_downside_review import StableDelayBacktest
from pension_structure_review import prepare
from portfolio import Portfolio
from result_paths import research_result_path
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from strategy30_31_yaml_parity import STRATEGY30
from strategy32_parity_review import STRATEGY32
from qqq_action_attribution import episodes
from qqq_action_value_probe import weighted_ridge, FEATURES

LOCK_PATH = PROJECT_ROOT/'validation/qqq_guarded_reentry_lock_20261026.json'
LOCK = json.loads(LOCK_PATH.read_text(encoding='utf-8'))
CAPITAL = LOCK['cost']['capital']


def make_source(number, proxy=False):
    source = DeclarativeStrategy(load_strategy_definition(STRATEGY30 if number == 30 else STRATEGY32))
    if proxy:
        source.parameters['valuation_arm_score'] = 30.
        source.parameters['valuation_breakdown_points'] = 4.
    return source


def load_case(directory, period):
    source = make_source(32, period != 'CURRENT')
    engine = StableDelayBacktest(source, data_dir=directory, tickers=source.required_tickers,
        start_date=LOCK['periods'][period][0], end_date=LOCK['periods'][period][1])
    return [{'Date': d, 'Open': engine.get_prices(row, 'Open'), 'Close': engine.get_prices(row, 'Close'),
             'Market': engine.get_market(engine.market_data.loc[d])} for d, row in engine.data.iterrows()]


def policy_inputs(source, signal, market):
    state = source.notification_context['state_values']
    target = float(signal['target']['QQQ'])
    blocked = (state.get('trend_mode') == 'BEAR' or state.get('defense_mode') == 'DEFENSE' or
               any(state.get(s) == 'TRUE' for s in ('credit_guard', 'deep_guard', 'financial_guard', 'structural_bear_mode')) or
               target <= 0 or target >= 1-1e-8)
    q = market['QQQ']
    values = [q.get(k) for k in ('Close', 'EMA20', 'ROC5')]
    gate = all(v is not None and np.isfinite(v) for v in values) and q['Close'] > q['EMA20'] and q['ROC5'] > 0
    return bool(blocked), bool(gate), state


def feature_values(source, market):
    q, v = market['QQQ'], source.variables
    return {FEATURES[0]: q['Close']/q['EMA200']-1,
            FEATURES[1]: v['relative_roc20']/100,
            FEATURES[2]: v['credit_change20']}


class ReentryPolicy:
    def __init__(self, profile):
        self.profile, self.active, self.previous_target = profile, False, None

    def amend(self, source, signal, market, portfolio, *, permit_entry=False):
        blocked, gate, state = policy_inputs(source, signal, market)
        was_active = self.active
        if self.profile == 'BASE' or blocked or not gate:
            self.active = False
        elif permit_entry:
            self.active = True
        target = dict(signal['target'])
        if self.active:
            target['QQQ'] += ((1-target['QQQ'])*.5 if self.profile == 'RULE_HALF' else .10)
            target['QQQ'] = min(1., target['QQQ'])
            target['BIL'] = 1-target['QQQ']
        transition = self.active != was_active
        changed = self.previous_target is not None and any(abs(target[s]-self.previous_target[s]) > 1e-10 for s in target)
        if self.active:
            actual = portfolio.weights({s: market[s]['Close'] for s in ('QQQ', 'BIL')})
            deviation = max(abs(actual.get(s, 0.)-target[s]) for s in target)
            rebalance = transition or changed or deviation >= .075 or actual.get('QQQ', 0.)-target['QQQ'] <= -.04
        else:
            rebalance = bool(signal['rebalance']) or transition
        if blocked and target != signal['target']:
            raise AssertionError('Hard-guard target changed')
        self.previous_target = dict(target)
        return {**signal, 'target': target, 'rebalance': rebalance,
                'reason': 'GUARDED_REENTRY' if self.active or transition else signal.get('reason')}, blocked, gate


def queue_order(pending, signal, i, delay, *, initial=False):
    if not initial and not signal['rebalance']:
        return pending
    if signal['days'] != 1:
        raise ValueError('One-session execution required')
    target = dict(signal['target'])
    if pending and all(abs(target.get(s, 0)-pending['Target'].get(s, 0)) < 1e-12 for s in set(target)|set(pending['Target'])):
        return pending
    return {'Due': i+1+(0 if initial else delay), 'Target': target, 'Decision': i,
            'Reason': signal.get('reason')}


def execute_due(portfolio, pending, i, row):
    if pending and pending['Due'] == i:
        portfolio.start_rebalance(pending['Target'], days=1, date=row['Date'], reason=pending['Reason'])
        portfolio.update(row['Open'], date=row['Date'])
        return None
    return pending


def clone_account(portfolio):
    result = Portfolio(commission=portfolio.commission, slippage=portfolio.slippage)
    result.positions, result.cash = dict(portfolio.positions), portfolio.cash
    return result


def branch_path(data, snapshot, *, enter=False, horizon=21, delay=0):
    """A single current action, then all source defenses keep running."""
    i0 = snapshot['Index']
    if i0+horizon >= len(data):
        return None
    source, p = deepcopy(snapshot['Source']), clone_account(snapshot['Portfolio'])
    policy = ReentryPolicy('RULE10' if enter else 'BASE')
    initial, _, _ = policy.amend(source, snapshot['Signal'], data[i0]['Market'], p, permit_entry=enter)
    pending = queue_order(deepcopy(snapshot.get('Pending')), initial, i0, delay)
    before = p.value(data[i0+1]['Open'])
    values, checked = [], 0
    for i in range(i0+1, i0+horizon+1):
        row = data[i]
        pending = execute_due(p, pending, i, row)
        signal = source.evaluate(row['Date'], row['Market'], p)
        amended, blocked, _ = policy.amend(source, signal, row['Market'], p)
        checked += int(blocked)
        pending = queue_order(pending, amended, i, delay)
        values.append(p.value(row['Close']))
    return {'Path': np.array(values)/before, 'Costs': p.total_costs/before, 'GuardChecks': checked}


def forecast(labels, date, values):
    if labels is None or labels.empty:
        return None, 0, 0
    train = labels.loc[(labels.EndDate < date) & (labels.EpisodeEnd < date)]
    if len(train) < LOCK['model']['min_rows'] or train.Episode.nunique() < LOCK['model']['min_completed_episodes']:
        return None, len(train), train.Episode.nunique()
    prediction, _ = weighted_ridge(train, pd.Series(values), LOCK['model']['ridge_alpha'])
    return prediction, len(train), train.Episode.nunique()


def simulate(data, number, period, profile, *, cost=1., delay=0, labels=None, collect=False):
    source = make_source(number, period != 'CURRENT')
    p = Portfolio(commission=LOCK['cost']['commission']*cost, slippage=LOCK['cost']['slippage']*cost)
    p.cash = CAPITAL
    policy, pending, week = ReentryPolicy(profile), None, None
    rows, snapshots = [], []
    for i, row in enumerate(data):
        d = row['Date']
        pending = execute_due(p, pending, i, row)
        signal = source.evaluate(d, row['Market'], p)
        original = dict(signal['target'])
        blocked, gate, state = policy_inputs(source, signal, row['Market'])
        current_week = d.isocalendar()[:2]
        weekly = current_week != week
        week = current_week
        eligible = weekly and not blocked and gate
        prediction, training_rows, training_episodes = None, 0, 0
        if eligible and collect:
            snapshots.append({'Index': i, 'Source': deepcopy(source), 'Signal': deepcopy(signal),
                              'Portfolio': clone_account(p), 'Pending': deepcopy(pending),
                              'Features': feature_values(source, row['Market'])})
        if eligible and profile == 'MODEL10' and period == 'CURRENT':
            prediction, training_rows, training_episodes = forecast(labels, d, feature_values(source, row['Market']))
        permit = eligible and (profile in ('RULE10', 'RULE_HALF') or profile == 'MODEL10' and prediction is not None and prediction > 0)
        amended, blocked, gate = policy.amend(source, signal, row['Market'], p, permit_entry=permit)
        pending = queue_order(pending, amended, i, delay, initial=i == 0)
        nav = p.value(row['Close'])
        if p.cash < -1e-5 or any(n < -1e-8 for n in p.positions.values()):
            raise AssertionError('Borrowing or short position')
        rows.append({'Date': d, 'Portfolio': nav/CAPITAL, 'Costs': p.total_costs/CAPITAL,
                     'QQQActual': p.weights(row['Close']).get('QQQ', 0.), 'SourceTarget': original['QQQ'],
                     'Target': amended['target']['QQQ'], 'Active': policy.active, 'Blocked': blocked,
                     'WeeklyEligible': eligible, 'EntryPermitted': permit, 'Prediction': prediction,
                     'TrainingRows': training_rows, 'TrainingEpisodes': training_episodes,
                     'Credit': state.get('credit_guard'), 'Deep': state.get('deep_guard'),
                     'Financial': state.get('financial_guard'), 'Trend': state.get('trend_mode')})
    trades = pd.DataFrame(p.trades)
    for column in ('Shares', 'Fee', 'Cash'):
        if column in trades:
            trades[column] /= CAPITAL
    return pd.DataFrame(rows).set_index('Date'), trades, snapshots


def build_labels(data, history, snapshots, delay):
    events = episodes(history.SourceTarget)
    rows = []
    for snapshot in snapshots:
        i = snapshot['Index']
        date = data[i]['Date']
        event = next(e for e in events if e['StartDecision'] <= date <= e['EndDecision'])
        control = branch_path(data, snapshot, delay=delay)
        treated = branch_path(data, snapshot, enter=True, delay=delay)
        if control is None:
            continue
        # Pure-source branch must agree with the actual baseline continuation.
        start_nav = snapshot['Portfolio'].value(data[i+1]['Open'])
        original = history.Portfolio.iloc[i+1:i+22].to_numpy()*CAPITAL/start_nav
        np.testing.assert_allclose(control['Path'], original, rtol=1e-10, atol=1e-10)
        rows.append({'DecisionDate': date, 'EndDate': data[i+21]['Date'], 'Episode': event['Episode'],
                     'EpisodeEnd': event['EndDecision'] if event['Closed'] else pd.NaT,
                     'DeltaToBase': treated['Path'][-1]-control['Path'][-1],
                     'BaseReturn': control['Path'][-1]-1, 'TreatmentReturn': treated['Path'][-1]-1,
                     'GuardChecks': treated['GuardChecks'], **snapshot['Features']})
    return pd.DataFrame(rows)


def summary(history, trades):
    nav = history.Portfolio
    years = (nav.index[-1]-nav.index[0]).days/365.25
    return {'CAGR': nav.iloc[-1]**(1/years)-1, 'MDD': (nav/nav.cummax()-1).min(),
            'Return': nav.iloc[-1]-1, 'TradeDates': pd.to_datetime(trades.Date).nunique() if len(trades) else 0,
            'Costs': history.Costs.iloc[-1], 'ActiveDays': int(history.Active.sum()),
            'Entries': int((history.Active & ~history.Active.shift(fill_value=False)).sum()),
            'EligibleWeeks': int(history.WeeklyEligible.sum()), 'Predictions': int(history.Prediction.notna().sum()),
            'MaxGuardIncrement': float((history.Target-history.SourceTarget).loc[history.Blocked].max()) if history.Blocked.any() else 0.}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=LOCK['scenarios'], default='MAIN')
    parser.add_argument('--periods', nargs='+', choices=LOCK['periods'], default=list(LOCK['periods']))
    parser.add_argument('--prefix-check', action='store_true')
    args = parser.parse_args()
    settings = LOCK['scenarios'][args.scenario]
    all_rows, checks = [], []
    inputs = [LOCK_PATH, Path(__file__), STRATEGY30, STRATEGY32,
              PROJECT_ROOT/'src/legacy-python/portfolio.py', PROJECT_ROOT/'src/legacy-python/backtest.py',
              PROJECT_ROOT/'src/legacy-python/strategy_dsl.py', Path(prepare.__code__.co_filename),
              PROJECT_ROOT/'src/legacy-python/validation/pension_downside_review.py',
              PROJECT_ROOT/'src/legacy-python/validation/qqq_action_value_probe.py',
              PROJECT_ROOT/'validation/qqq_action_value_probe_lock_20261026.json']
    inputs += [DATA_DIR/(s+'.csv') for s in ('QQQ', 'BIL', 'SPY', 'KRW=X', 'shiller_cape')]
    inputs += [EXTENDED_DATA_DIR/(s+'.csv') for s in ('QQQ', 'BIL')]+[PROJECT_ROOT/'tmp/BAA10Y.csv', PROJECT_ROOT/'tmp/XLF.csv']
    for period in args.periods:
        with TemporaryDirectory(prefix='qqq-guarded-', dir=PROJECT_ROOT/'tmp') as temp:
            directory = prepare(Path(temp), proxy=period != 'CURRENT')
            data = load_case(directory, period)
            for number in LOCK['sources']:
                baseline, _, snapshots = simulate(data, number, period, 'BASE', collect=period == 'CURRENT', **settings)
                native_source = make_source(number, period != 'CURRENT')
                native = StableDelayBacktest(native_source, data_dir=directory, tickers=native_source.required_tickers,
                    start_date=data[0]['Date'], end_date=data[-1]['Date'], commission=LOCK['cost']['commission']*settings['cost'],
                    slippage=LOCK['cost']['slippage']*settings['cost'], signal_delay_days=settings['delay'])
                native.portfolio.cash = CAPITAL
                native_history = native.run_all()[0]
                np.testing.assert_allclose(baseline.Portfolio, native_history.Portfolio/CAPITAL, rtol=1e-10, atol=1e-10)
                checks.append({'Period': period, 'Source': number, 'NativeBaselineParity': True})
                labels = build_labels(data, baseline, snapshots, settings['delay']) if period == 'CURRENT' else None
                stamp = f'qqq_guarded_{args.scenario.lower()}_{period.lower()}_{number}'
                if labels is not None:
                    labels.to_csv(research_result_path(stamp+'_labels.csv'), index=False)
                for profile in LOCK['profiles']:
                    h, trades, _ = simulate(data, number, period, profile, labels=labels, **settings)
                    h.to_csv(research_result_path(stamp+'_'+profile.lower()+'_daily.csv'))
                    trades.to_csv(research_result_path(stamp+'_'+profile.lower()+'_trades.csv'), index=False)
                    row = {'Period': period, 'Source': number, 'Scenario': args.scenario, 'Profile': profile, **summary(h, trades)}
                    all_rows.append(row)
                    pd.DataFrame(all_rows).to_csv(research_result_path('qqq_guarded_'+args.scenario.lower()+'_summary.csv'), index=False)
                    print(row, flush=True)
                    if args.prefix_check:
                        cutoff = next((i for i, r in enumerate(data) if r['Date'] >= pd.Timestamp('2023-01-03')), len(data)//2)
                        prefix_labels = labels.loc[labels.DecisionDate <= data[cutoff]['Date']] if labels is not None and len(labels) else labels
                        prefix = simulate(data[:cutoff+1], number, period, profile, labels=prefix_labels, **settings)[0]
                        pd.testing.assert_frame_equal(prefix, h.iloc[:cutoff+1])
                        checks.append({'Period': period, 'Source': number, 'Profile': profile, 'PrefixPassed': True})
            prepared = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.csv')}
            manifest = {'Arguments': vars(args), 'Design': LOCK, 'Checks': checks, 'PreparedSHA256': prepared,
                        'InputSHA256': {str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
            research_result_path(f'qqq_guarded_{args.scenario.lower()}_{period.lower()}_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
