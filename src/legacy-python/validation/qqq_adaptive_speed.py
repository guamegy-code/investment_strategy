"""Conditional confirmation speed and guarded reentry: closed-loop factorial study."""
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
import qqq_guarded_reentry as core
from config import PROJECT_ROOT, DATA_DIR, EXTENDED_DATA_DIR
from portfolio import Portfolio
from result_paths import research_result_path

LOCK_PATH = PROJECT_ROOT/'validation/qqq_adaptive_speed_lock_20261027.json'
LOCK = json.loads(LOCK_PATH.read_text(encoding='utf-8'))
PROTECTED = ('credit_guard', 'deep_guard', 'deep_credit_peak', 'financial_guard', 'structural_bear_mode')


def momentum_features(qqq, bil):
    """Trailing total returns only; preserve missing observations without filling."""
    cash = bil.Close.reindex(qqq.index)
    out = pd.DataFrame(index=qqq.index)
    for name, horizon in [('TurnFastExcess', LOCK['momentum']['fast_sessions']),
                          ('TurnSlowExcess', LOCK['momentum']['slow_sessions'])]:
        out[name] = qqq.Close.pct_change(horizon, fill_method=None)-cash.pct_change(horizon, fill_method=None)
    return out


class AdaptiveSource:
    """Change only confirmation lengths; delegate all native state/target logic."""
    def __init__(self, source, speed):
        if speed not in ('NONE', 'SELL', 'BUY', 'BOTH', 'ALWAYS'):
            raise ValueError(speed)
        self.source, self.speed = source, speed
        self.down_days = self.up_days = 0
        self.sell_fast = self.buy_fast = False
        self.accelerated = []
        self.rules = []
        for name in ('trend_mode', 'defense_mode'):
            for rule in source.definition['state'][name]['rules']:
                original = int(rule.get('confirm', 1))
                if original <= 1:
                    continue
                side, fast = None, original
                if name == 'trend_mode' and rule['set'] == 'BEAR':
                    side, fast = 'SELL', LOCK['sell_speed']['trend_mode_to_BEAR']['accelerated']
                elif name == 'defense_mode' and rule['set'] == 'DEFENSE':
                    side, fast = 'SELL', LOCK['sell_speed']['defense_mode_WARNING_to_DEFENSE']['accelerated']
                elif name == 'trend_mode' and rule['set'] in ('RECOVERY', 'BULL'):
                    side, fast = 'BUY', LOCK['buy_speed']['trend_mode_BEAR_to_RECOVERY']['accelerated']
                if side:
                    self.rules.append((name, rule, original, side, fast))

    def __getattr__(self, name):
        return getattr(self.source, name)

    def evaluate(self, date, market, portfolio):
        fast = market['QQQ'].get('TurnFastExcess', np.nan)
        slow = market['QQQ'].get('TurnSlowExcess', np.nan)
        valid = fast is not None and slow is not None and np.isfinite(fast) and np.isfinite(slow)
        down = valid and fast < 0 < slow
        up = valid and slow < 0 < fast
        self.down_days = self.down_days+1 if down else 0
        self.up_days = self.up_days+1 if up else 0
        n = LOCK['momentum']['confirmation_sessions']
        self.sell_fast = self.speed == 'ALWAYS' or self.speed in ('SELL', 'BOTH') and self.down_days >= n
        self.buy_fast = self.speed == 'ALWAYS' or self.speed in ('BUY', 'BOTH') and self.up_days >= n
        before = dict(self.source._state_values)
        candidates = deepcopy(self.source._state_candidates)
        for _, rule, original, side, quick in self.rules:
            enabled = self.sell_fast if side == 'SELL' else self.buy_fast
            rule['confirm'] = quick if enabled else original
        signal = self.source.evaluate(date, market, portfolio)
        self.accelerated = []
        for name, rule, original, side, quick in self.rules:
            after = self.source._state_values[name]
            if name not in self.source._changed_state or after != rule['set'] or rule['confirm'] >= original:
                continue
            # A different, immediate native rule may share the same destination.
            # In particular NORMAL->DEFENSE must not be attributed to the
            # shortened WARNING->DEFENSE rule; initialization is also native.
            if before[name] == 'UNINITIALIZED' or name == 'defense_mode' and before[name] != 'WARNING':
                continue
            candidate = candidates.get(name)
            days = candidate['days']+1 if candidate and candidate['value'] == after else 1
            if days < original:
                event = f'{side}:{name}:{before[name]}->{after}'
                if event not in self.accelerated:
                    self.accelerated.append(event)
        return signal


def make_source(number, period, speed):
    return AdaptiveSource(core.make_source(number, period != 'CURRENT'), speed)


def load_case(directory, period):
    data = core.load_case(directory, period)
    qqq = pd.read_csv(directory/'QQQ.csv', index_col='Date', parse_dates=True)
    bil = pd.read_csv(directory/'BIL.csv', index_col='Date', parse_dates=True)
    features = momentum_features(qqq, bil)
    cut = data[len(data)//2]['Date']
    pd.testing.assert_frame_equal(features.loc[:cut], momentum_features(qqq.loc[:cut], bil.loc[:cut]))
    for row in data:
        row['Market']['QQQ'].update(features.loc[row['Date']].to_dict())
    return data


def simulate(data, number, period, profile, *, cost=1., delay=0):
    config = LOCK['profiles'][profile]
    source = make_source(number, period, config['speed'])
    p = Portfolio(commission=LOCK['cost']['commission']*cost, slippage=LOCK['cost']['slippage']*cost)
    p.cash = LOCK['cost']['capital']
    policy = core.ReentryPolicy('RULE_HALF' if config['reentry'] else 'BASE')
    pending, week, rows = None, None, []
    for i, row in enumerate(data):
        date = row['Date']
        pending = core.execute_due(p, pending, i, row)
        signal = source.evaluate(date, row['Market'], p)
        blocked, gate, state = core.policy_inputs(source, signal, row['Market'])
        current_week = date.isocalendar()[:2]
        eligible = current_week != week and not blocked and gate
        week = current_week
        permit = eligible and config['reentry']
        amended, blocked, _ = policy.amend(source, signal, row['Market'], p, permit_entry=permit)
        pending = core.queue_order(pending, amended, i, delay, initial=i == 0)
        if p.cash < -1e-5 or any(n < -1e-8 for n in p.positions.values()):
            raise AssertionError('Borrowing or short position')
        q = row['Market']['QQQ']
        rows.append({'Date': date, 'Portfolio': p.value(row['Close'])/core.CAPITAL,
                     'Costs': p.total_costs/core.CAPITAL, 'QQQActual': p.weights(row['Close']).get('QQQ', 0.),
                     'SourceTarget': signal['target']['QQQ'], 'Target': amended['target']['QQQ'],
                     'Active': policy.active, 'Blocked': blocked, 'WeeklyEligible': eligible,
                     'EntryPermitted': permit, 'Prediction': None, 'TrainingRows': 0, 'TrainingEpisodes': 0,
                     'Credit': state.get('credit_guard'), 'Deep': state.get('deep_guard'),
                     'Financial': state.get('financial_guard'), 'Trend': state.get('trend_mode'),
                     'Defense': state.get('defense_mode'), 'Structural': state.get('structural_bear_mode'),
                     'ValuationPeak': state.get('valuation_peak'),
                     'FastExcess': q.get('TurnFastExcess'), 'SlowExcess': q.get('TurnSlowExcess'),
                     'DownDays': source.down_days, 'UpDays': source.up_days,
                     'SellFast': source.sell_fast, 'BuyFast': source.buy_fast,
                     'Accelerated': '|'.join(source.accelerated)})
    trades = pd.DataFrame(p.trades)
    for column in ('Shares', 'Fee', 'Cash'):
        if column in trades:
            trades[column] /= core.CAPITAL
    return pd.DataFrame(rows).set_index('Date'), trades


def summary(history, trades):
    return {**core.summary(history, trades), 'AcceleratedTransitions': int(history.Accelerated.ne('').sum()),
            'SellSpeedDays': int(history.SellFast.sum()), 'BuySpeedDays': int(history.BuyFast.sum())}


def stamp(scenario, period, number, profile):
    return f'qqq_adaptive_{scenario.lower()}_{period.lower()}_{number}_{profile.lower()}'


def input_paths():
    return [LOCK_PATH, Path(__file__), Path(core.__file__), core.LOCK_PATH, core.STRATEGY30, core.STRATEGY32,
            PROJECT_ROOT/'src/legacy-python/portfolio.py', PROJECT_ROOT/'src/legacy-python/backtest.py',
            PROJECT_ROOT/'src/legacy-python/strategy_dsl.py', Path(core.prepare.__code__.co_filename),
            PROJECT_ROOT/'src/legacy-python/validation/pension_downside_review.py',
            PROJECT_ROOT/'src/legacy-python/validation/strategy28_failed_dip_regime.py',
            PROJECT_ROOT/'src/legacy-python/validation/strategy30_31_yaml_parity.py',
            PROJECT_ROOT/'src/legacy-python/validation/strategy30_xlf_conditional_review.py',
            PROJECT_ROOT/'validation/pension_structure_lock_20261025.json',
            PROJECT_ROOT/'src/legacy-python/downloader.py', PROJECT_ROOT/'src/legacy-python/indicators.py',
            *[DATA_DIR/(s+'.csv') for s in ('QQQ', 'BIL', 'SPY', 'KRW=X', 'shiller_cape')],
            *[EXTENDED_DATA_DIR/(s+'.csv') for s in ('QQQ', 'BIL')],
            PROJECT_ROOT/'tmp/BAA10Y.csv', PROJECT_ROOT/'tmp/XLF.csv']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=LOCK['scenarios'], default='MAIN')
    parser.add_argument('--periods', nargs='+', choices=LOCK['periods'])
    parser.add_argument('--prefix-check', action='store_true')
    args = parser.parse_args()
    periods = args.periods or (list(LOCK['periods']) if args.scenario == 'MAIN' else ['CURRENT'])
    profiles = list(LOCK['profiles']) if args.scenario == 'MAIN' else LOCK['stress_profiles']
    settings = LOCK['scenarios'][args.scenario]
    for period in periods:
        checks, rows = [], []
        with TemporaryDirectory(prefix='qqq-adaptive-', dir=PROJECT_ROOT/'tmp') as temp:
            directory = core.prepare(Path(temp), proxy=period != 'CURRENT')
            data = load_case(directory, period)
            checks.append({'FeaturePrefix': True})
            for number in LOCK['sources']:
                for profile in profiles:
                    history, trades = simulate(data, number, period, profile, **settings)
                    if profile in ('BASE', 'REENTRY'):
                        reference = core.simulate(data, number, period, 'BASE' if profile == 'BASE' else 'RULE_HALF', **settings)
                        pd.testing.assert_frame_equal(history[reference[0].columns], reference[0])
                        pd.testing.assert_frame_equal(trades, reference[1])
                        checks.append({'Source': number, 'Profile': profile, 'PriorRunnerParity': True})
                    name = stamp(args.scenario, period, number, profile)
                    history.to_csv(research_result_path(name+'_daily.csv'))
                    trades.to_csv(research_result_path(name+'_trades.csv'), index=False)
                    row = {'Period': period, 'Source': number, 'Scenario': args.scenario,
                           'Profile': profile, **summary(history, trades)}
                    rows.append(row)
                    print(row, flush=True)
                    if args.prefix_check:
                        cut = next((i for i, r in enumerate(data) if r['Date'] >= pd.Timestamp('2023-01-03')), len(data)//2)
                        prefix = simulate(data[:cut+1], number, period, profile, **settings)[0]
                        pd.testing.assert_frame_equal(prefix, history.iloc[:cut+1])
                        checks.append({'Source': number, 'Profile': profile, 'PrefixPassed': True})
            prepared = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.csv')}
        case = f'qqq_adaptive_{args.scenario.lower()}_{period.lower()}'
        pd.DataFrame(rows).to_csv(research_result_path(case+'_summary.csv'), index=False)
        manifest = {'Arguments': vars(args), 'Design': LOCK, 'Checks': checks, 'PreparedSHA256': prepared,
                    'InputSHA256': {str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths()}}
        research_result_path(case+'_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
