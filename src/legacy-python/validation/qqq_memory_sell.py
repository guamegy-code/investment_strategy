"""Early valuation selling with the original valuation memory left intact."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qqq_adaptive_speed as adaptive
from portfolio import Portfolio
from result_paths import research_result_path

core = adaptive.core
PROJECT_ROOT = adaptive.PROJECT_ROOT
LOCK_PATH = PROJECT_ROOT/'validation/qqq_memory_sell_lock_20261028.json'
LOCK = json.loads(LOCK_PATH.read_text(encoding='utf-8'))
MEMORY_COLUMNS = ['Defense', 'ValuationPeak', 'Credit', 'Deep', 'Financial', 'Structural']


def make_source(number, period, profile):
    settings = LOCK['profiles'][profile]
    source = adaptive.make_source(number, period, 'BOTH' if settings['trend_sell'] else 'BUY')
    # The native valuation state and its counters remain completely unmodified.
    source.rules = [item for item in source.rules if item[0] != 'defense_mode']
    return source


class EarlyValuationSell:
    def __init__(self, enabled):
        self.enabled, self.active, self.recovery_days = enabled, False, 0
        self.event = ''

    def evaluate(self, source, market):
        self.event = ''
        state = source.notification_context['state_values']
        defense = state['defense_mode']
        if not self.enabled:
            self.active, self.recovery_days = False, 0
            return False
        if self.active:
            if defense != 'WARNING':
                self.active, self.recovery_days = False, 0
                self.event = 'HANDOFF' if defense == 'DEFENSE' else 'NATIVE_CLEAR'
            else:
                q = market['QQQ']
                recovery = source.variables['recovery_score'] >= 4 and q['Close'] > q['EMA20']
                self.recovery_days = self.recovery_days+1 if recovery else 0
                if self.recovery_days >= 2:
                    self.active, self.recovery_days = False, 0
                    self.event = 'RECOVERY_RELEASE'
        elif defense == 'WARNING' and source.down_days >= 2:
            candidate = source._state_candidates.get('defense_mode', {})
            if candidate.get('value') == 'DEFENSE' and candidate.get('days', 0) >= 2:
                self.active, self.recovery_days = True, 0
                self.event = 'ENTRY'
        return self.active


class MemorySellPolicy:
    def __init__(self, enabled):
        self.early = EarlyValuationSell(enabled)
        self.reentry = core.ReentryPolicy('RULE_HALF')
        self.previous_target = None

    def amend(self, source, signal, market, portfolio, *, permit_entry=False):
        was_early = self.early.active
        active = self.early.evaluate(source, market)
        # BASE cancels an already active reentry as well as suppressing a new one.
        self.reentry.profile = 'BASE' if active else 'RULE_HALF'
        amended, blocked, gate = self.reentry.amend(source, signal, market, portfolio,
                                                    permit_entry=permit_entry and not active)
        target = dict(amended['target'])
        if active:
            target['QQQ'] = min(float(signal['target']['QQQ']), LOCK['valuation_overlay']['cap'])
            target['BIL'] = 1-target['QQQ']
        rebalance = bool(amended['rebalance'])
        if active or was_early:
            changed = self.previous_target is None or any(abs(target[s]-self.previous_target[s]) > 1e-10 for s in target)
            actual = portfolio.weights({s: market[s]['Close'] for s in ('QQQ', 'BIL')})
            deviation = max(abs(actual.get(s, 0.)-target[s]) for s in target)
            rebalance = changed or deviation >= .075 or actual.get('QQQ', 0.)-target['QQQ'] <= -.04
        if blocked and target['QQQ'] > signal['target']['QQQ']+1e-12:
            raise AssertionError('Protected native target increased')
        if active and self.reentry.active:
            raise AssertionError('Reentry conflicts with early selling')
        self.previous_target = dict(target)
        return {**amended, 'target': target, 'rebalance': bool(rebalance),
                'reason': 'MEMORY_PRESERVING_EARLY_SELL' if active or was_early else amended.get('reason')}, blocked, gate


def simulate(data, number, period, profile, *, cost=1., delay=0):
    source = make_source(number, period, profile)
    p = Portfolio(commission=LOCK['cost']['commission']*cost, slippage=LOCK['cost']['slippage']*cost)
    p.cash = LOCK['cost']['capital']
    policy = MemorySellPolicy(LOCK['profiles'][profile]['valuation_overlay'])
    week, pending, rows = None, None, []
    for i, row in enumerate(data):
        date = row['Date']
        pending = core.execute_due(p, pending, i, row)
        signal = source.evaluate(date, row['Market'], p)
        blocked, gate, state = core.policy_inputs(source, signal, row['Market'])
        current_week = date.isocalendar()[:2]
        eligible = current_week != week and not blocked and gate
        week = current_week
        amended, blocked, _ = policy.amend(source, signal, row['Market'], p, permit_entry=eligible)
        pending = core.queue_order(pending, amended, i, delay, initial=i == 0)
        if p.cash < -1e-5 or any(n < -1e-8 for n in p.positions.values()):
            raise AssertionError('Borrowing or short position')
        candidate = source._state_candidates.get('defense_mode', {})
        rows.append({'Date': date, 'Portfolio': p.value(row['Close'])/core.CAPITAL,
                     'Costs': p.total_costs/core.CAPITAL, 'QQQActual': p.weights(row['Close']).get('QQQ', 0.),
                     'SourceTarget': signal['target']['QQQ'], 'Target': amended['target']['QQQ'],
                     'Active': policy.reentry.active, 'Blocked': blocked, 'WeeklyEligible': eligible,
                     'EarlySell': policy.early.active, 'EarlyEvent': policy.early.event,
                     'EarlyRecoveryDays': policy.early.recovery_days,
                     'NativeDefenseConfirm': candidate.get('days', 0) if candidate.get('value') == 'DEFENSE' else 0,
                     'Prediction': None, 'Credit': state.get('credit_guard'), 'Deep': state.get('deep_guard'),
                     'Financial': state.get('financial_guard'), 'Structural': state.get('structural_bear_mode'),
                     'Trend': state.get('trend_mode'), 'Defense': state.get('defense_mode'),
                     'ValuationPeak': state.get('valuation_peak'), 'DownDays': source.down_days,
                     'UpDays': source.up_days, 'Accelerated': '|'.join(source.accelerated)})
    trades = pd.DataFrame(p.trades)
    for column in ('Shares', 'Fee', 'Cash'):
        if column in trades:
            trades[column] /= core.CAPITAL
    return pd.DataFrame(rows).set_index('Date'), trades


def summary(h, trades):
    return {**core.summary(h, trades), 'EarlySellDays': int(h.EarlySell.sum()),
            'EarlyEntries': int(h.EarlyEvent.eq('ENTRY').sum()),
            'RecoveryReleases': int(h.EarlyEvent.eq('RECOVERY_RELEASE').sum()),
            'NativeHandoffs': int(h.EarlyEvent.eq('HANDOFF').sum())}


def stamp(scenario, period, number, profile):
    return f'qqq_memory_{scenario.lower()}_{period.lower()}_{number}_{profile.lower()}'


def archived(scenario, period, number, profile):
    return pd.read_csv(research_result_path(adaptive.stamp(scenario, period, number, profile)+'_daily.csv'),
                       index_col='Date', parse_dates=True)


def verify_archive(scenario, period):
    path = research_result_path(f'qqq_adaptive_{scenario.lower()}_{period.lower()}_manifest.json')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    for name, digest in manifest['InputSHA256'].items():
        if hashlib.sha256((PROJECT_ROOT/name).read_bytes()).hexdigest() != digest:
            raise AssertionError(f'Changed comparison input: {name}')
    evaluation = json.loads(research_result_path('qqq_adaptive_evaluation.json').read_text(encoding='utf-8'))
    for number in LOCK['sources']:
        for profile in ('REENTRY_BUY', 'COMBINED'):
            for suffix in ('daily', 'trades'):
                output = research_result_path(adaptive.stamp(scenario, period, number, profile)+f'_{suffix}.csv')
                if hashlib.sha256(output.read_bytes()).hexdigest() != evaluation['OutputSHA256'][output.name]:
                    raise AssertionError(f'Changed archived result: {output.name}')
    return path


def input_paths():
    return [*adaptive.input_paths(), LOCK_PATH, Path(__file__),
            PROJECT_ROOT/'src/legacy-python/validation/qqq_adaptive_direction_stress.py',
            PROJECT_ROOT/'validation/qqq_adaptive_direction_stress_20261027.json',
            research_result_path('qqq_adaptive_evaluation.json')]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=LOCK['scenarios'], default='MAIN')
    parser.add_argument('--periods', nargs='+', choices=LOCK['periods'])
    parser.add_argument('--prefix-check', action='store_true')
    args = parser.parse_args()
    periods = args.periods or (list(LOCK['periods']) if args.scenario == 'MAIN' else ['CURRENT'])
    settings = LOCK['scenarios'][args.scenario]
    for period in periods:
        archive_path = verify_archive(args.scenario, period)
        rows, checks = [], []
        with TemporaryDirectory(prefix='qqq-memory-', dir=PROJECT_ROOT/'tmp') as temp:
            directory = core.prepare(Path(temp), proxy=period != 'CURRENT')
            data = adaptive.load_case(directory, period)
            checks.append({'FeaturePrefix': True})
            for number in LOCK['sources']:
                reference = None
                for profile in LOCK['profiles']:
                    h, trades = simulate(data, number, period, profile, **settings)
                    if profile == 'REFERENCE':
                        reference = h
                        old = archived(args.scenario, period, number, 'REENTRY_BUY')
                        np.testing.assert_allclose(h[['Portfolio', 'Costs', 'SourceTarget', 'Target', 'QQQActual']],
                                                   old[['Portfolio', 'Costs', 'SourceTarget', 'Target', 'QQQActual']], rtol=1e-12, atol=1e-12)
                        checks.append({'Source': number, 'ArchivedReferenceParity': True})
                    pd.testing.assert_frame_equal(h[MEMORY_COLUMNS], reference[MEMORY_COLUMNS], check_exact=True)
                    checks.append({'Source': number, 'Profile': profile, 'ValuationAndGuardsPreserved': True})
                    name = stamp(args.scenario, period, number, profile)
                    h.to_csv(research_result_path(name+'_daily.csv'))
                    trades.to_csv(research_result_path(name+'_trades.csv'), index=False)
                    row = {'Period': period, 'Scenario': args.scenario, 'Source': number, 'Profile': profile, **summary(h, trades)}
                    rows.append(row)
                    print(row, flush=True)
                    if args.prefix_check:
                        cut = next((i for i, r in enumerate(data) if r['Date'] >= pd.Timestamp('2023-01-03')), len(data)//2)
                        prefix = simulate(data[:cut+1], number, period, profile, **settings)[0]
                        pd.testing.assert_frame_equal(prefix, h.iloc[:cut+1], check_exact=True)
                        checks.append({'Source': number, 'Profile': profile, 'PrefixPassed': True})
            prepared = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.csv')}
        archive_manifest = json.loads(archive_path.read_text(encoding='utf-8'))
        if prepared != archive_manifest['PreparedSHA256']:
            raise AssertionError('Prepared inputs differ from archived comparison')
        case = f'qqq_memory_{args.scenario.lower()}_{period.lower()}'
        pd.DataFrame(rows).to_csv(research_result_path(case+'_summary.csv'), index=False)
        manifest = {'Design': LOCK, 'Arguments': vars(args), 'Checks': checks, 'PreparedSHA256': prepared,
                    'InputSHA256': {str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in [*input_paths(), archive_path]}}
        research_result_path(case+'_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
