"""Verify Strategy 33 against the frozen REENTRY_BUY research paths.

Writes ignored fixtures for the browser/Worker parity test. No data downloads.
Run with PYTHONPATH=src/legacy-python, then node tests/web/strategy33_parity.mjs.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qqq_adaptive_speed as research
from portfolio import Portfolio
from strategy_dsl import DeclarativeStrategy, load_strategy_definition

ROOT = research.PROJECT_ROOT
STRATEGY33 = ROOT / 'strategies/33_qqq_guarded_reentry_buy_acceleration.yaml'
OUT = ROOT / 'results/research/qqq/strategy33'
PROTECTED = ('trend_mode', 'defense_mode', 'valuation_peak', 'credit_guard',
             'deep_guard', 'deep_credit_peak', 'financial_guard', 'structural_bear_mode')


def run_case(data, period, scenario, settings):
    definition = load_strategy_definition(STRATEGY33)
    source = DeclarativeStrategy(definition)
    if period != 'CURRENT':
        source.parameters.update(valuation_arm_score=30., valuation_breakdown_points=4.)
    expected = research.make_source(32, period, 'BUY')
    policy = research.core.ReentryPolicy('RULE_HALF')
    portfolio = Portfolio(commission=.00015 * settings['cost'], slippage=.00020 * settings['cost'])
    portfolio.cash = research.core.CAPITAL
    pending, week = None, None
    records, fixtures = [], []
    for i, row in enumerate(data):
        date, market = row['Date'], row['Market']
        pending = research.core.execute_due(portfolio, pending, i, row)
        native = expected.evaluate(date, market, portfolio)
        current_week = date.isocalendar()[:2]
        reference, blocked, _ = policy.amend(expected, native, market, portfolio,
                                             permit_entry=current_week != week)
        week = current_week
        actual = source.evaluate(date, market, portfolio)
        for field in ('target', 'rebalance', 'days'):
            assert actual[field] == reference[field], (period, scenario, date, field, actual[field], reference[field])
        for name in PROTECTED:
            assert source._state_values[name] == expected._state_values[name], (date, name)
        assert source._state_values['recovery_turn_days'] == expected.up_days, date
        assert (source._state_values['guarded_reentry'] == 'TRUE') == policy.active, date
        if blocked:
            assert actual['target'] == native['target'], date
        if scenario == 'MAIN':
            fixtures.append({'date': str(date.date()), 'market': market,
                'weights': portfolio.weights(row['Close']), 'target': actual['target'],
                'rebalance': actual['rebalance'], 'state': deepcopy(source._state_values)})
        pending = research.core.queue_order(pending, actual, i, settings['delay'], initial=i == 0)
        records.append({'Date': date, 'Portfolio': portfolio.value(row['Close']) / research.core.CAPITAL,
                        'Target': actual['target']['QQQ'], 'Active': policy.active})
    history = pd.DataFrame(records).set_index('Date')
    archived = pd.read_csv(ROOT / 'results/research/qqq' /
        (research.stamp(scenario, period, 32, 'REENTRY_BUY') + '_daily.csv'), index_col='Date', parse_dates=True)
    assert history.index.equals(archived.index)
    np.testing.assert_allclose(history[['Portfolio', 'Target']], archived[['Portfolio', 'Target']], rtol=1e-10, atol=1e-10)
    history.to_csv(OUT / f'{scenario.lower()}_{period.lower()}.csv')
    if fixtures:
        # JSON null is used only for unavailable indicators; runtimes map it to NaN.
        definition['parameters'] = dict(source.parameters)
        payload = {'definition': definition, 'rows': fixtures}
        raw = json.dumps(payload, default=float, ensure_ascii=False)
        raw = raw.replace('NaN', 'null').replace('Infinity', 'null')
        (OUT / f'{period.lower()}_fixture.json').write_text(raw, encoding='utf-8')
    nav = history.Portfolio
    result = {'period': period, 'scenario': scenario, 'sessions': len(history),
              'CAGR': nav.iloc[-1] ** (365.25 / (nav.index[-1]-nav.index[0]).days)-1,
              'MDD': (nav/nav.cummax()-1).min(),
              'trade_dates': len({str(t['Date']) for t in portfolio.trades}),
              'archive_parity': True, 'state_signal_parity': True}
    print(json.dumps(result), flush=True)
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for period in research.LOCK['periods']:
        with TemporaryDirectory(prefix='strategy33-', dir=ROOT/'tmp') as temp:
            directory = research.core.prepare(Path(temp), proxy=period != 'CURRENT')
            data = research.load_case(directory, period)
            for scenario, settings in research.LOCK['scenarios'].items():
                if period != 'CURRENT' and scenario != 'MAIN':
                    continue
                results.append(run_case(data, period, scenario, settings))
    (OUT/'parity.json').write_text(json.dumps(results, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
