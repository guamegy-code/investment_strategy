"""Exact QQQ relative-wealth attribution and fixed-horizon action labels."""
from __future__ import annotations

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
from config import DATA_DIR, EXTENDED_DATA_DIR, PROJECT_ROOT
from pension_downside_review import StableDelayBacktest
from pension_structure_review import prepare
from portfolio import Portfolio
from result_paths import research_result_path
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from strategy30_31_yaml_parity import STRATEGY30
from strategy32_parity_review import STRATEGY32

LOCK_PATH = PROJECT_ROOT/'validation/qqq_action_attribution_lock_20261026.json'
LOCK = json.loads(LOCK_PATH.read_text(encoding='utf-8'))
CAPITAL = LOCK['capital']
ASSETS = ('QQQ', 'BIL')


def account(positions, cash):
    p = Portfolio(commission=LOCK['commission'], slippage=LOCK['slippage'])
    p.cash, p.positions = float(cash), dict(positions)
    return p


def prices_on(prices, date, field):
    return {s: float(prices[s].at[date, field]) for s in ASSETS}


def nav_path(portfolio, prices, dates, target=None):
    p = deepcopy(portfolio)
    start = p.value(prices_on(prices, dates[0], 'Open'))
    if target is not None:
        p.rebalance(prices_on(prices, dates[0], 'Open'), target, date=dates[0])
    values = [p.value(prices_on(prices, d, 'Close')) for d in dates]
    return np.array(values)/start, p.total_costs-portfolio.total_costs


def relative_attribution(history, prices):
    """Telescope exact account PnL against QQQ, with equal initial entries."""
    first = next(i for i, p in enumerate(history.Positions) if p.get('QQQ', 0.) > 0)
    if first != 1 or history.iloc[first].Positions.get('BIL', 0.) != 0:
        raise ValueError('Matched initial all-QQQ entry required')
    passive = history.iloc[first].Portfolio
    rows = []
    for i, (date, now) in enumerate(history.iterrows()):
        if i <= first:
            rows.append({'Date': date, 'PassiveQQQ': now.Portfolio, 'GapAllocation': 0.,
                         'DayAllocation': 0., 'Friction': 0., 'AllocationGain': 0., 'AllocationDrag': 0.})
            continue
        prev = history.iloc[i-1]
        opens, closes = prices_on(prices, date, 'Open'), prices_on(prices, date, 'Close')
        old = prices_on(prices, history.index[i-1], 'Close')
        gap_pnl = sum(prev.Positions.get(s, 0.)*(opens[s]-old[s]) for s in ASSETS)
        open_nav = prev.Cash+sum(prev.Positions.get(s, 0.)*opens[s] for s in ASSETS)
        day_pnl = sum(now.Positions.get(s, 0.)*(closes[s]-opens[s]) for s in ASSETS)
        cost = now.TransactionCosts-prev.TransactionCosts
        np.testing.assert_allclose(now.Portfolio, prev.Portfolio+gap_pnl-cost+day_pnl, rtol=1e-11)
        q_gap, q_day = opens['QQQ']/old['QQQ']-1, closes['QQQ']/opens['QQQ']-1
        gap_active = (gap_pnl-prev.Portfolio*q_gap)*(1+q_day)
        day_active = day_pnl-(open_nav-cost)*q_day
        friction = -cost*(1+q_day)
        passive *= (1+q_gap)*(1+q_day)
        np.testing.assert_allclose(now.Portfolio-prev.Portfolio*(1+q_gap)*(1+q_day),
                                   gap_active+day_active+friction, rtol=1e-10, atol=1e-6)
        g, d = gap_active/passive, day_active/passive
        rows.append({'Date': date, 'PassiveQQQ': passive, 'GapAllocation': g, 'DayAllocation': d,
                     'Friction': friction/passive, 'AllocationGain': max(g, 0)+max(d, 0),
                     'AllocationDrag': min(g, 0)+min(d, 0)})
    result = pd.DataFrame(rows).set_index('Date')
    contribution = result[['GapAllocation', 'DayAllocation', 'Friction']].sum().sum()
    np.testing.assert_allclose(contribution, history.Portfolio.iloc[-1]/result.PassiveQQQ.iloc[-1]-1,
                               rtol=1e-10, atol=1e-10)
    return result


def episodes(target):
    """Only close information defines membership; future end is descriptive."""
    result, active = [], None
    for i, (date, weight) in enumerate(target.items()):
        if weight < 1-1e-8 and active is None:
            active = {'Episode': len(result)+1, 'StartDecision': date, 'StartIndex': i}
        elif weight >= 1-1e-8 and active is not None:
            result.append({**active, 'EndDecision': date, 'EndIndex': i, 'Closed': True})
            active = None
    if active is not None:
        result.append({**active, 'EndDecision': target.index[-1], 'EndIndex': len(target)-1, 'Closed': False})
    return result


def episode_at(date, episode_list):
    for event in episode_list:
        if event['StartDecision'] <= date <= event['EndDecision']:
            return event['Episode']
    return None


def action_outcomes(history, prices, decision_i, execution_i, submitted, kind, context, cluster):
    prev = history.iloc[execution_i-1]
    p = account(prev.Positions, prev.Cash)
    opens = prices_on(prices, history.index[execution_i], 'Open')
    before = p.value(opens)
    actual = p.weights(opens).get('QQQ', 0.)
    policies = {'HOLD': None}
    if kind in ('SELL', 'BUY'):
        policies['HALF_ADJUST'] = actual+(submitted-actual)/2
    else:
        policies['HALF_REENTRY'] = actual+(1-actual)/2
    if kind in ('BUY', 'WEEKLY_REENTRY'):
        policies['FULL_REENTRY'] = 1.
    rows = []
    for horizon in LOCK['horizons_sessions']:
        end = execution_i+horizon-1
        common = {'DecisionDate': history.index[decision_i], 'ExecutionDate': history.index[execution_i],
                  'Kind': kind, 'Episode': cluster, 'Horizon': horizon, 'ActualRiskAtOpen': actual,
                  'SubmittedRisk': submitted, 'State': context.get('trend_mode'),
                  'CreditGuard': context.get('credit_guard'), 'DeepGuard': context.get('deep_guard'),
                  'ExecutionOpenNAV': before/CAPITAL}
        if end >= len(history):
            rows.append({**common, 'Policy': 'ALL', 'Complete': False})
            continue
        dates = history.index[execution_i:end+1]
        baseline = history.Portfolio.iloc[execution_i:end+1].to_numpy()/before
        rows.append({**common, 'Policy': 'FOLLOW_BASE', 'Complete': True, 'EndDate': dates[-1],
                     'Return': baseline[-1]-1, 'DeltaToBase': 0.,
                     'MDD': float((np.r_[1., baseline]/np.maximum.accumulate(np.r_[1., baseline])-1).min()),
                     'Costs': (history.TransactionCosts.iloc[end]-prev.TransactionCosts)/before})
        for policy, weight in policies.items():
            path, cost = nav_path(p, prices, dates, None if weight is None else {'QQQ': weight, 'BIL': 1-weight})
            rows.append({**common, 'Policy': policy, 'Complete': True, 'EndDate': dates[-1],
                         'Return': path[-1]-1, 'DeltaToBase': path[-1]-baseline[-1],
                         'MDD': float((np.r_[1., path]/np.maximum.accumulate(np.r_[1., path])-1).min()),
                         'Costs': cost/before})
    return rows


def analyze(history, trades, rebalances, prices):
    target = history.NotificationContext.map(lambda c: c['target_weights']['QQQ'])
    events = episodes(target)
    attributed = relative_attribution(history, prices)
    event_rows = []
    for event in events:
        start = event['StartIndex']+1
        if start >= len(history):
            continue
        end = min(event['EndIndex']+1, len(history)-1)
        prior, dates = history.iloc[start-1], history.index[start:end+1]
        p = account(prior.Positions, prior.Cash)
        start_nav = p.value(prices_on(prices, dates[0], 'Open'))
        hold, _ = nav_path(p, prices, dates)
        baseline = history.Portfolio.iloc[start:end+1].to_numpy()/start_nav
        piece = attributed.loc[dates]
        trough = int(np.argmin(hold))  # Retrospective attribution only.
        event_rows.append({k: v for k, v in event.items() if not k.endswith('Index')} | {
            'ExecutionStart': dates[0], 'ExecutionEnd': dates[-1], 'Sessions': len(dates),
            'BaseReturn': baseline[-1]-1, 'HoldReturn': hold[-1]-1,
            'BaseMinusHold': baseline[-1]-hold[-1],
            'RelativeWealthVsHold': baseline[-1]/hold[-1]-1,
            'BaseMDD': float((np.r_[1., baseline]/np.maximum.accumulate(np.r_[1., baseline])-1).min()),
            'HoldMDD': float((np.r_[1., hold]/np.maximum.accumulate(np.r_[1., hold])-1).min()),
            'HoldTrough': dates[trough], 'AdvantageAtHoldTrough': baseline[trough]/hold[trough]-1,
            'RelativeAdvantageAfterTrough': (baseline[-1]/baseline[trough])/(hold[-1]/hold[trough])-1,
            'AllocationGain': piece.AllocationGain.sum(), 'AllocationDrag': piece.AllocationDrag.sum(),
            'Friction': piece.Friction.sum()})
    actions, action_rows = [], []
    # Actual executed target and pretrade account identify trade actions, not
    # retrospectively selected price peaks. Initial funding is not a buy signal.
    seen = set()
    for rebalance in rebalances:
        d = rebalance.get('ExecutionDate')
        if d is None or d in seen:
            continue
        seen.add(d)
        i = history.index.get_loc(d)
        if i <= 1:
            continue
        daily = trades.loc[pd.to_datetime(trades.Date) == d]
        delta = float(daily.loc[daily.Ticker == 'QQQ', 'Shares'].sum())
        if abs(delta) < 1e-8:
            continue
        kind = 'BUY' if delta > 0 else 'SELL'
        decision_i = i-1
        context = history.NotificationContext.iloc[decision_i]['state_values']
        cluster = episode_at(history.index[decision_i], events)
        rows = action_outcomes(history, prices, decision_i, i, rebalance['Target']['QQQ'], kind, context, cluster)
        action_rows.extend(rows)
        actions.append({'ExecutionDate': d, 'DecisionDate': history.index[decision_i], 'Kind': kind,
                        'Episode': cluster, 'QQQSharesChange': delta/CAPITAL,
                        'SubmittedRisk': rebalance['Target']['QQQ'], **context})
    weekly = []
    last_week = None
    for i, d in enumerate(history.index[:-1]):
        week = d.isocalendar()[:2]
        if week == last_week:
            continue
        last_week = week
        current_risk = history.Weights.iloc[i].get('QQQ', 0.)
        if target.iloc[i] >= 1-1e-8 or current_risk >= .999:
            continue
        weekly.extend(action_outcomes(history, prices, i, i+1, target.iloc[i], 'WEEKLY_REENTRY',
            history.NotificationContext.iloc[i]['state_values'], episode_at(d, events)))
    return attributed, pd.DataFrame(event_rows), pd.DataFrame(actions), pd.DataFrame(action_rows), pd.DataFrame(weekly)


def main():
    summary, all_events, all_actions, all_weekly, checks, inputs = [], [], [], [], [], [LOCK_PATH, Path(__file__),
        STRATEGY30, STRATEGY32, PROJECT_ROOT/'src/legacy-python/portfolio.py',
        PROJECT_ROOT/'src/legacy-python/backtest.py', PROJECT_ROOT/'src/legacy-python/strategy_dsl.py',
        Path(prepare.__code__.co_filename), PROJECT_ROOT/'src/legacy-python/validation/pension_downside_review.py']
    original_paths = {s: DATA_DIR/(s+'.csv') for s in ('QQQ', 'BIL', 'SPY', 'KRW=X', 'shiller_cape')}
    inputs += list(original_paths.values())+[PROJECT_ROOT/'tmp/BAA10Y.csv', PROJECT_ROOT/'tmp/XLF.csv']
    inputs += [EXTENDED_DATA_DIR/(s+'.csv') for s in ASSETS]
    for period, dates in LOCK['periods'].items():
        with TemporaryDirectory(prefix='qqq-actions-', dir=PROJECT_ROOT/'tmp') as temp:
            directory = prepare(Path(temp), proxy=period != 'CURRENT')
            prepared_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.csv')}
            price = {s: pd.read_csv(directory/(s+'.csv'), index_col='Date', parse_dates=True) for s in ASSETS}
            paths = {}
            for number in LOCK['strategies']:
                source = DeclarativeStrategy(load_strategy_definition(STRATEGY30 if number == 30 else STRATEGY32))
                if period != 'CURRENT':
                    source.parameters['valuation_arm_score'] = 30.
                    source.parameters['valuation_breakdown_points'] = 4.
                engine = StableDelayBacktest(source, data_dir=directory, tickers=source.required_tickers,
                    start_date=dates[0], end_date=dates[1], commission=LOCK['commission'], slippage=LOCK['slippage'])
                engine.portfolio.cash = CAPITAL
                history, trades, rebalances = engine.run_all()
                key = {'Period': period, 'Source': number}
                stamp = f'qqq_actions_{period.lower()}_{number}'
                attr, events, actions, labels, weekly = analyze(history, trades, rebalances, price)
                paths[number] = history.Portfolio/CAPITAL
                # The source state/price paths must reproduce the previously
                # reviewed engine, apart from minute fractional-share roundoff.
                legacy_path = research_result_path(f'pension_structure_{"current" if period == "CURRENT" else "crises"}_{period.lower()}_base{number}_daily.csv')
                legacy = pd.read_csv(legacy_path, index_col='Date', parse_dates=True).Portfolio
                pd.testing.assert_index_equal(history.index, legacy.index)
                np.testing.assert_allclose(history.Portfolio/CAPITAL, legacy, rtol=2e-8, atol=2e-8)
                checks.append({**key, 'PreviousBaselineReproduced': True})
                inputs.append(legacy_path)
                for name, frame in [('attribution', attr), ('episodes', events), ('actions', actions), ('labels', labels), ('weekly', weekly)]:
                    frame.to_csv(research_result_path(stamp+'_'+name+'.csv'), index=name == 'attribution')
                all_events.append(events.assign(**key))
                all_actions.append(labels.assign(**key))
                all_weekly.append(weekly.assign(**key))
                years = (history.index[-1]-history.index[0]).days/365.25
                summary.append({**key, 'CAGR': (history.Portfolio.iloc[-1]/CAPITAL)**(1/years)-1,
                    'MDD': (history.Portfolio/history.Portfolio.cummax()-1).min(), 'Episodes': len(events),
                    'TradeActions': len(actions), 'WeeklyDecisions': weekly.ExecutionDate.nunique(),
                    'AllocationGain': attr.AllocationGain.sum(), 'AllocationDrag': attr.AllocationDrag.sum(),
                    'Friction': attr.Friction.sum(),
                    'RelativeWealthVsQQQ': history.Portfolio.iloc[-1]/attr.PassiveQQQ.iloc[-1]-1})
                print(summary[-1], flush=True)
            if period == 'CURRENT':
                pd.testing.assert_series_equal(paths[30], paths[32], check_names=False)
                checks.append({'Period': period, 'Source30And32DailyIdentical': True})
            research_result_path(f'qqq_actions_{period.lower()}_prepared.json').write_text(json.dumps(prepared_hashes, indent=2), encoding='utf-8')
    for name, frames in [('summary', [pd.DataFrame(summary)]), ('episodes', all_events), ('labels', all_actions), ('weekly', all_weekly)]:
        pd.concat(frames, ignore_index=True).to_csv(research_result_path('qqq_actions_all_'+name+'.csv'), index=False)
    manifest = {'Design': LOCK, 'Checks': checks, 'InputSHA256': {
        str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
    research_result_path('qqq_actions_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
