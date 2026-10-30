"""Evaluate partial selling and invalidation as a predeclared 2x2 experiment."""
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qqq_partial_sell as study
from qqq_adaptive_speed_evaluation import window_stats
from result_paths import research_result_path

LOCK = study.LOCK


def daily(scenario, period, number, profile):
    return pd.read_csv(research_result_path(study.stamp(scenario, period, number, profile)+'_daily.csv'),
                       index_col='Date', parse_dates=True)


def main():
    cases = [('MAIN', p) for p in LOCK['periods']]
    cases += [(s, 'CURRENT') for s in LOCK['scenarios'] if s != 'MAIN']
    summaries, checks, verified, manifests = [], [], 0, {}
    for scenario, period in cases:
        case = f'qqq_partial_{scenario.lower()}_{period.lower()}'
        summary = pd.read_csv(research_result_path(case+'_summary.csv'))
        expected = {(s, p) for s in LOCK['sources'] for p in LOCK['profiles']}
        if set(zip(summary.Source, summary.Profile)) != expected or len(summary) != len(expected):
            raise AssertionError(f'Incomplete or duplicate matrix: {case}')
        if not summary.Scenario.eq(scenario).all() or not summary.Period.eq(period).all():
            raise AssertionError('Mislabeled case')
        summaries.append(summary)
        path = research_result_path(case+'_manifest.json')
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifests[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        if manifest['Design'] != LOCK:
            raise AssertionError('Design changed after execution')
        for name, digest in manifest['InputSHA256'].items():
            if hashlib.sha256((study.PROJECT_ROOT/name).read_bytes()).hexdigest() != digest:
                raise AssertionError(f'Input changed: {name}')
            verified += 1
        checks.extend({'Scenario': scenario, 'Period': period, **c} for c in manifest['Checks'])
        study.verify_archive(scenario, period)
    summary = pd.concat(summaries, ignore_index=True)
    keys = ['Scenario', 'Period', 'Source']
    comparison = summary.copy()
    for name in ('REFERENCE', 'FULL_SLOW'):
        reference = summary.loc[summary.Profile.eq(name), keys+['CAGR', 'MDD', 'Return']]
        comparison = comparison.merge(reference, on=keys, suffixes=('', '_'+name), validate='many_to_one')
        comparison['CAGRDelta_'+name] = comparison.CAGR-comparison['CAGR_'+name]
        comparison['MDDDelta_'+name] = comparison.MDD-comparison['MDD_'+name]
        comparison['RelativeFinalWealth_'+name] = (1+comparison.Return)/(1+comparison['Return_'+name])-1
    comparison.to_csv(research_result_path('qqq_partial_comparison.csv'), index=False)
    windows, annual, events, interactions, effects, verdicts, equal_current = [], [], [], [], [], [], []
    for scenario, period in cases:
        for number in LOCK['sources']:
            paths = {p: daily(scenario, period, number, p) for p in LOCK['profiles']}
            base = paths['REFERENCE']
            for profile, h in paths.items():
                columns = [*study.memory.MEMORY_COLUMNS, 'Trend']
                pd.testing.assert_frame_equal(h[columns], base[columns], check_exact=True)
                if (h.Target < -1e-12).any() or (h.Target > 1+1e-12).any():
                    raise AssertionError('Invalid target')
                if ((h.Target-h.SourceTarget).loc[h.Blocked] > 1e-12).any():
                    raise AssertionError('Protected target increased')
                if (h.EarlySell & h.Active).any():
                    raise AssertionError('Early sell and reentry active together')
                for date, row in h.loc[h.EarlyEvent.notna()].iterrows():
                    events.append({'Scenario': scenario, 'Period': period, 'Source': number, 'Profile': profile,
                                   'Date': date, 'Event': row.EarlyEvent, 'Defense': row.Defense,
                                   'ValuationPeak': row.ValuationPeak, 'NativeDefenseConfirm': row.NativeDefenseConfirm,
                                   'SourceTarget': row.SourceTarget, 'Target': row.Target,
                                   'ReferenceTarget': base.loc[date, 'Target']})
                if scenario != 'MAIN' or period != 'CURRENT':
                    continue
                for start, end in LOCK['gate']['subperiods']:
                    a, b = window_stats(h.Portfolio.loc[start:end]), window_stats(base.Portfolio.loc[start:end])
                    windows.append({'Source': number, 'Profile': profile, 'Start': start, 'End': end, **a,
                                    'CAGRDelta': a['CAGR']-b['CAGR'], 'MDDDelta': a['MDD']-b['MDD']})
                for reference in ('REFERENCE', 'FULL_SLOW'):
                    ratio = h.Portfolio/paths[reference].Portfolio
                    factors = (ratio/ratio.shift(1)).fillna(1.).groupby(ratio.index.year).prod()
                    np.testing.assert_allclose(factors.prod(), ratio.iloc[-1]/ratio.iloc[0], rtol=1e-10, atol=1e-10)
                    for year, factor in factors.items():
                        annual.append({'Source': number, 'Profile': profile, 'Reference': reference, 'Year': year,
                                       'RelativeWealthContribution': factor-1,
                                       'RelativeWealthWithoutYearAttribution': ratio.iloc[-1]/factor-1})
            for treatment, control, meaning in [('HALF_SLOW', 'FULL_SLOW', 'Half size under slow release'),
                                               ('FULL_FAST', 'FULL_SLOW', 'Fast release under full size'),
                                               ('HALF_FAST', 'HALF_SLOW', 'Fast release under half size'),
                                               ('HALF_FAST', 'FULL_FAST', 'Half size under fast release')]:
                a, b = paths[treatment], paths[control]
                sa, sb = window_stats(a.Portfolio), window_stats(b.Portfolio)
                effects.append({'Scenario': scenario, 'Period': period, 'Source': number,
                                'Treatment': treatment, 'Control': control, 'Meaning': meaning,
                                'CAGRDelta': sa['CAGR']-sb['CAGR'], 'MDDDelta': sa['MDD']-sb['MDD'],
                                'RelativeFinalWealth': a.Portfolio.iloc[-1]/b.Portfolio.iloc[-1]-1})
            effect = (np.log(paths['HALF_FAST'].Portfolio.iloc[-1]/paths['FULL_FAST'].Portfolio.iloc[-1])-
                      np.log(paths['HALF_SLOW'].Portfolio.iloc[-1]/paths['FULL_SLOW'].Portfolio.iloc[-1]))
            interactions.append({'Scenario': scenario, 'Period': period, 'Source': number,
                                 'LogWealthInteraction': effect, 'InteractionFactorMinusOne': np.expm1(effect)})
        if period == 'CURRENT':
            for profile in LOCK['profiles']:
                a, b = daily(scenario, period, 30, profile), daily(scenario, period, 32, profile)
                columns = ['Portfolio', 'Target', 'Active', 'EarlySell', 'Trend', 'Defense', 'ValuationPeak']
                equal_current.append({'Scenario': scenario, 'Profile': profile, 'Identical': a[columns].equals(b[columns])})
    for number in LOCK['sources']:
        for profile in ('HALF_SLOW', 'FULL_FAST', 'HALF_FAST'):
            selected = comparison.loc[(comparison.Source == number) & (comparison.Profile == profile)]
            main_row = selected.loc[(selected.Scenario == 'MAIN') & (selected.Period == 'CURRENT')].iloc[0]
            stress = selected.loc[selected.Scenario != 'MAIN']
            proxies = selected.loc[selected.Period != 'CURRENT']
            gate = LOCK['gate']
            passed = (main_row.CAGRDelta_REFERENCE > 1e-10 and
                      main_row.MDDDelta_REFERENCE >= -gate['current_mdd_deterioration_max']-1e-10 and
                      stress.CAGRDelta_REFERENCE.min() > 1e-10 and
                      proxies.MDDDelta_REFERENCE.min() >= -gate['each_proxy_mdd_deterioration_max']-1e-10)
            verdicts.append({'Source': number, 'Profile': profile, 'MainCAGRDelta': main_row.CAGRDelta_REFERENCE,
                             'MainMDDDelta': main_row.MDDDelta_REFERENCE,
                             'WorstStressCAGRDelta': stress.CAGRDelta_REFERENCE.min(),
                             'WorstProxyMDDDelta': proxies.MDDDelta_REFERENCE.min(), 'IncrementalGatePass': bool(passed)})
    for name, records in [('windows', windows), ('annual_contributions', annual), ('events', events),
                           ('factor_effects', effects), ('interactions', interactions), ('verdict', verdicts)]:
        pd.DataFrame(records).to_csv(research_result_path('qqq_partial_'+name+'.csv'), index=False)
    columns = ['ValuationPeak', 'Defense', 'NativeDefenseConfirm', 'SourceTarget', 'Target',
               'EarlyEvent', 'QQQActual', 'Portfolio']
    trace = pd.concat([daily('MAIN', 'CURRENT', 30, p)[columns].add_prefix(p+'_') for p in LOCK['profiles']], axis=1)
    trace.loc['2019-10-01':'2020-04-14'].to_csv(research_result_path('qqq_partial_valuation_trace.csv'))
    record = {'RunCount': len(summary), 'InputHashReferencesVerified': verified,
              'ArchivedParityChecks': sum(c.get('ArchivedParity', False) for c in checks),
              'PrefixChecks': sum(c.get('PrefixPassed', False) for c in checks),
              'FeaturePrefixChecks': sum(c.get('FeaturePrefix', False) for c in checks),
              'NativeStatePreservedChecks': sum(c.get('ValuationAndGuardsPreserved', False) for c in checks),
              'CurrentSourceComparisons': equal_current,
              'EvaluationSHA256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'ManifestSHA256': manifests}
    for key, expected in [('RunCount', 60), ('ArchivedParityChecks', 24), ('PrefixChecks', 30),
                          ('FeaturePrefixChecks', 6), ('NativeStatePreservedChecks', 60)]:
        if record[key] != expected:
            raise AssertionError(f'Incomplete checks: {key}={record[key]}')
    outputs = sorted(research_result_path('qqq_partial_evaluation.json').parent.glob('qqq_partial_*.csv'))
    record['OutputSHA256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs}
    research_result_path('qqq_partial_evaluation.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(pd.DataFrame(verdicts).to_string(index=False))
    print(comparison.loc[(comparison.Source == 30) & (comparison.Period == 'CURRENT'),
          ['Scenario', 'Profile', 'CAGR', 'MDD', 'TradeDates', 'CAGRDelta_REFERENCE', 'RelativeFinalWealth_REFERENCE']].to_string(index=False))
    print({k: v for k, v in record.items() if k not in ('OutputSHA256', 'ManifestSHA256', 'CurrentSourceComparisons')})


if __name__ == '__main__':
    main()
