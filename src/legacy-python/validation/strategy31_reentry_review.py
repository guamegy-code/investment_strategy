"""Evaluate the fixed pension reentry adaptation against Strategy 31 version 2.

No search/optimization; original TDF target and hard guards remain binding.
Historical TDF/credit/cash proxies are research inputs, not actual ETF returns.
"""
from copy import deepcopy
import argparse
import json
from pathlib import Path
from shutil import copy2
import subprocess
import sys
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qqq_adaptive_speed as adaptive
from portfolio import Portfolio
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from tdf_proxy import build_proxy_frame

ROOT = adaptive.PROJECT_ROOT
PATH = ROOT/'strategies/31_band_7030_tdf_valuation_credit_guard_no_topup.yaml'
OUT = ROOT/'results/research/qqq/strategy31_reentry'
BASE_REVISION = 'd5b3ac1'
DEPLOYED = ROOT/'strategies/34_band_7030_tdf_credit_guard_reentry.yaml'


def baseline_definition():
    text = subprocess.check_output(['git', 'show', f'{BASE_REVISION}:strategies/{PATH.name}'], cwd=ROOT).decode('utf-8')
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT/'baseline_v2.yaml'
    path.write_text(text, encoding='utf-8')
    return load_strategy_definition(path)


def combined_definition(base):
    """Build the fixed transfer independently of the subsequently adopted YAML."""
    personal = load_strategy_definition(ROOT/'strategies/33_qqq_guarded_reentry_buy_acceleration.yaml')
    d = deepcopy(base)
    d['strategy'].update(version=3, name='70/30 TDF Credit Guard Reentry Acceleration (31) [BIL]')
    states = {}
    for name, config in d['state'].items():
        if name == 'trend_mode':
            states['recovery_turn_days'] = deepcopy(personal['state']['recovery_turn_days'])
            config = deepcopy(personal['state']['trend_mode'])
        states[name] = config
    for key in ('native_qqq_target', 'reentry_week', 'guarded_reentry', 'reentry_target'):
        states[key] = deepcopy(personal['state'][key])
    rules = []
    for rule in base['target']:
        q = rule['weights']['QQQ']
        value = float(q[:-1])/100 if q.endswith('%') else '='+q
        rules.append({**({'when': rule['when'].strip()} if 'when' in rule else {'otherwise': True}), 'set': value})
    states['native_qqq_target'] = {'initial': .7, 'rules': rules}
    condition = states['guarded_reentry']['rules'][0]['when']
    states['guarded_reentry']['rules'][0]['when'] = condition.replace(" or state.financial_guard == 'TRUE'", '').replace('0.99999999', '0.69999999')
    states['reentry_target']['initial'] = .7
    states['reentry_target']['rules'][0]['set'] = '=state.native_qqq_target + (0.7 - state.native_qqq_target) * 0.5'
    d['state'] = states
    d['target'] = [{'when': "state.guarded_reentry == 'TRUE'", 'weights': {
        'QQQ': 'state.reentry_target', 'TDF2050_PROXY': '30%', 'BIL': '70% - state.reentry_target'}}, *d['target']]
    d['rebalance'] = deepcopy(personal['rebalance'])
    d['rebalance'][0]['when'] = d['rebalance'][0]['when'].replace(' or changed(state.financial_guard)', '')
    d['notifications']['states'] = {'guarded_reentry': deepcopy(personal['notifications']['states']['guarded_reentry']), **d['notifications']['states']}
    return d


def selected_definition(base):
    """The selected rules are published as 34, preserving 31 as the baseline.

    Historical output filenames retain v3 as the research variant label.
    """
    candidate = combined_definition(base)
    candidate['state']['trend_mode'] = deepcopy(base['state']['trend_mode'])
    candidate['state'].pop('recovery_turn_days')
    deployed = load_strategy_definition(DEPLOYED)

    def normalize(value):
        if isinstance(value, str): return ' '.join(value.split())
        if isinstance(value, list): return [normalize(item) for item in value]
        if isinstance(value, dict): return {key: normalize(item) for key, item in value.items()}
        return value

    assert normalize({k: v for k, v in candidate.items() if k != 'strategy'}) == normalize(
        {k: v for k, v in deployed.items() if k != 'strategy'}), '34 differs from the selected research rules'
    return candidate


def run(data, definition, base, settings, improved, fixtures=False, buy_only=False, reentry_only=False):
    source = DeclarativeStrategy(deepcopy(definition if improved else base))
    oracle = adaptive.AdaptiveSource(DeclarativeStrategy(deepcopy(base)), 'NONE' if reentry_only else 'BUY') if improved else None
    portfolio = Portfolio(commission=.00015*settings['cost'], slippage=.00020*settings['cost'])
    portfolio.cash = adaptive.core.CAPITAL
    pending, week, active, previous_target = None, None, False, None
    rows, fixture_rows = [], []
    for i, row in enumerate(data):
        date, market = row['Date'], row['Market']
        pending = adaptive.core.execute_due(portfolio, pending, i, row)
        signal = source.evaluate(date, market, portfolio)
        if oracle:
            native = oracle.evaluate(date, market, portfolio)
            state = oracle._state_values
            q = market['QQQ']
            blocked = (state['trend_mode'] == 'BEAR' or state['defense_mode'] == 'DEFENSE'
                or any(state[key] == 'TRUE' for key in ('credit_guard', 'deep_guard', 'structural_bear_mode'))
                or native['target']['QQQ'] <= 0 or native['target']['QQQ'] >= .7-1e-8)
            gate = np.isfinite([q['Close'], q['EMA20'], q['ROC5']]).all() and q['Close'] > q['EMA20'] and q['ROC5'] > 0
            was_active = active
            current_week = date.isocalendar()[:2]
            if buy_only or blocked or not gate:
                active = False
            elif current_week != week:
                active = True
            week = current_week
            target = dict(native['target'])
            if active:
                addition = min(target['BIL'], max(0, .7-target['QQQ']))*.5
                target['QQQ'] += addition
                target['BIL'] -= addition
            actual = portfolio.weights(row['Close'])
            changed = previous_target is not None and any(abs(target[s]-previous_target[s]) > 1e-10 for s in target)
            transition = active != was_active
            rebalance = (transition or changed or max(abs(actual.get(s, 0)-target[s]) for s in target) >= .075
                or actual.get('QQQ', 0)-target['QQQ'] <= -.04) if active else native['rebalance'] or transition
            if i == 0:
                rebalance = False  # Initial allocation is scheduled by the runner.
            previous_target = target
            np.testing.assert_allclose(list(signal['target'].values()), list(target.values()), atol=1e-12, rtol=0)
            assert signal['rebalance'] == rebalance, (date, signal['rebalance'], rebalance)
            for key, value in state.items():
                assert source._state_values[key] == value, (date, key)
            assert (source._state_values.get('guarded_reentry') == 'TRUE') == active
            assert signal['target']['QQQ'] <= .7+1e-12
            assert signal['target']['TDF2050_PROXY'] == native['target']['TDF2050_PROXY']
            if blocked:
                assert signal['target'] == native['target']
            if fixtures:
                fixture_rows.append({'date': str(date.date()), 'market': market,
                    'weights': actual, 'target': signal['target'], 'rebalance': signal['rebalance'],
                    'state': deepcopy(source._state_values)})
        pending = adaptive.core.queue_order(pending, signal, i, settings['delay'], initial=i == 0)
        rows.append({'Date': date, 'Portfolio': portfolio.value(row['Close'])/adaptive.core.CAPITAL,
            'QQQTarget': signal['target']['QQQ'], 'TDFTarget': signal['target']['TDF2050_PROXY'], 'Active': active})
    h = pd.DataFrame(rows).set_index('Date')
    nav = h.Portfolio
    result = {'CAGR': nav.iloc[-1]**(365.25/(nav.index[-1]-nav.index[0]).days)-1,
        'MDD': (nav/nav.cummax()-1).min(), 'TradeDates': len({str(t['Date']) for t in portfolio.trades}),
        'ActiveDays': int(h.Active.sum()), 'MaxQQQTarget': h.QQQTarget.max()}
    return result, h, fixture_rows


def main():
    global OUT
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--buy-only', action='store_true')
    mode.add_argument('--reentry-only', action='store_true')
    args = parser.parse_args()
    base = baseline_definition()
    candidate = combined_definition(base)
    if args.buy_only:
        OUT = OUT/'buy_only'
        OUT.mkdir(parents=True, exist_ok=True)
        candidate = deepcopy(base)
        states = {}
        for name, config in base['state'].items():
            if name == 'trend_mode':
                upgraded = combined_definition(base)
                states['recovery_turn_days'] = upgraded['state']['recovery_turn_days']
                config = upgraded['state']['trend_mode']
            states[name] = config
        candidate['state'] = states
        candidate['strategy']['version'] = 3
    if args.reentry_only:
        OUT = OUT/'reentry_only'
        OUT.mkdir(parents=True, exist_ok=True)
        candidate = selected_definition(base)
    results = []
    for period in adaptive.LOCK['periods']:
        proxies = ['ACTUAL_COMPONENTS'] if period == 'CURRENT' else ['SPY', 'QQQ']
        for proxy in proxies:
            with TemporaryDirectory(prefix='strategy31-reentry-', dir=ROOT/'tmp') as temp:
                directory = adaptive.core.prepare(Path(temp), proxy=period != 'CURRENT')
                if period == 'CURRENT':
                    copy2(adaptive.DATA_DIR/'TDF2050_PROXY.csv', directory/'TDF2050_PROXY.csv')
                else:
                    read = lambda p: pd.read_csv(p, index_col='Date', parse_dates=True)
                    spy = read(adaptive.DATA_DIR/'SPY.csv')
                    equity = spy if proxy == 'SPY' else read(adaptive.EXTENDED_DATA_DIR/'QQQ.csv')
                    tdf = build_proxy_frame({'SPY': spy, 'VXUS': equity,
                        'BND': read(adaptive.EXTENDED_DATA_DIR/'BND.csv')}, observation_lag=1)
                    tdf.to_csv(directory/'TDF2050_PROXY.csv', index_label='Date')
                b, c = deepcopy(base), deepcopy(candidate)
                if period != 'CURRENT':
                    for d in (b, c):
                        d['parameters'].update(valuation_arm_score=30., valuation_breakdown_points=4.)
                s = DeclarativeStrategy(c)
                engine = adaptive.core.StableDelayBacktest(s, data_dir=directory, tickers=s.required_tickers,
                    start_date=adaptive.LOCK['periods'][period][0], end_date=adaptive.LOCK['periods'][period][1])
                data = [{'Date': d, 'Open': engine.get_prices(row, 'Open'), 'Close': engine.get_prices(row, 'Close'),
                    'Market': engine.get_market(engine.market_data.loc[d])} for d, row in engine.data.iterrows()]
                for row in data:
                    q, bil = row['Market']['QQQ'], row['Market']['BIL']
                    q.update(TurnFastExcess=(q['ROC20']-bil['ROC20'])/100, TurnSlowExcess=(q['ROC252']-bil['ROC252'])/100)
                for scenario, settings in adaptive.LOCK['scenarios'].items():
                    if period != 'CURRENT' and scenario != 'MAIN': continue
                    for improved in (False, True):
                        result, h, fixture = run(data, c, b, settings, improved,
                            fixtures=improved and scenario == 'MAIN', buy_only=args.buy_only,
                            reentry_only=args.reentry_only)
                        label = f'{period.lower()}_{proxy.lower()}_{scenario.lower()}_{"v3" if improved else "v2"}'
                        h.to_csv(OUT/(label+'.csv'))
                        if fixture:
                            raw = json.dumps({'definition': c, 'rows': fixture}, default=float, ensure_ascii=False).replace('NaN','null')
                            (OUT/(label+'_fixture.json')).write_text(raw, encoding='utf-8')
                        result.update(Period=period, Proxy=proxy, Scenario=scenario, Version=3 if improved else 2, Sessions=len(h))
                        results.append(result)
                        print(json.dumps(result), flush=True)
    (OUT/'evaluation.json').write_text(json.dumps(results, indent=2), encoding='utf-8')


if __name__ == '__main__': main()
