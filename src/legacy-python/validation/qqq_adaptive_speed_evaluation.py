"""Evaluate the fixed adaptive-speed factorial matrix against both baselines."""
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PROJECT_ROOT
from result_paths import research_result_path
from qqq_adaptive_speed import LOCK, stamp


def daily(scenario, period, number, profile):
    return pd.read_csv(research_result_path(stamp(scenario, period, number, profile)+'_daily.csv'),
                       index_col='Date', parse_dates=True)


def window_stats(nav):
    years = (nav.index[-1]-nav.index[0]).days/365.25
    return {'CAGR': (nav.iloc[-1]/nav.iloc[0])**(1/years)-1,
            'MDD': (nav/nav.cummax()-1).min()}


def main():
    cases = [('MAIN', p) for p in LOCK['periods']]
    cases += [(s, 'CURRENT') for s in LOCK['scenarios'] if s != 'MAIN']
    summaries, verified, checks = [], 0, []
    for scenario, period in cases:
        case = f'qqq_adaptive_{scenario.lower()}_{period.lower()}'
        summary = pd.read_csv(research_result_path(case+'_summary.csv'))
        expected = list(LOCK['profiles']) if scenario == 'MAIN' else LOCK['stress_profiles']
        if len(summary) != len(LOCK['sources'])*len(expected):
            raise AssertionError(f'Incomplete matrix: {case}')
        if set(summary.Profile) != set(expected) or summary.duplicated(['Source', 'Profile']).any():
            raise AssertionError(f'Duplicate/missing profile: {case}')
        if summary.MaxGuardIncrement.abs().max() > 1e-12:
            raise AssertionError('Reentry changed a hard-protected target')
        summaries.append(summary)
        manifest = json.loads(research_result_path(case+'_manifest.json').read_text(encoding='utf-8'))
        if manifest['Design'] != LOCK:
            raise AssertionError('Design changed after execution')
        for name, digest in manifest['InputSHA256'].items():
            if hashlib.sha256((PROJECT_ROOT/name).read_bytes()).hexdigest() != digest:
                raise AssertionError(f'Input changed: {name}')
            verified += 1
        checks.extend({'Scenario': scenario, 'Period': period, **c} for c in manifest['Checks'])
    supplement = pd.read_csv(research_result_path('qqq_adaptive_direction_stress_summary.csv'))
    if len(supplement) != 12 or supplement.duplicated(['Scenario', 'Source', 'Profile']).any():
        raise AssertionError('Incomplete directional stress matrix')
    if set(supplement.Profile) != {'REENTRY_SELL', 'REENTRY_BUY'}:
        raise AssertionError('Unexpected supplemental profiles')
    supplemental_manifest = json.loads(research_result_path('qqq_adaptive_direction_stress_manifest.json').read_text(encoding='utf-8'))
    if supplemental_manifest['PrimaryDesign'] != LOCK:
        raise AssertionError('Supplement changed original design')
    supplemental_lock = PROJECT_ROOT/'validation/qqq_adaptive_direction_stress_20261027.json'
    if supplemental_manifest['Design'] != json.loads(supplemental_lock.read_text(encoding='utf-8')):
        raise AssertionError('Supplemental design mismatch')
    main_prepared = json.loads(research_result_path('qqq_adaptive_main_current_manifest.json').read_text(encoding='utf-8'))['PreparedSHA256']
    if supplemental_manifest['PreparedSHA256'] != main_prepared:
        raise AssertionError('Supplement uses different prices')
    for name, digest in supplemental_manifest['InputSHA256'].items():
        if hashlib.sha256((PROJECT_ROOT/name).read_bytes()).hexdigest() != digest:
            raise AssertionError(f'Changed supplemental input: {name}')
        verified += 1
    summaries.append(supplement)
    summary = pd.concat(summaries, ignore_index=True)
    keys = ['Scenario', 'Period', 'Source']
    comparison = summary.copy()
    for profile in ('BASE', 'REENTRY'):
        reference = summary.loc[summary.Profile == profile, keys+['CAGR', 'MDD', 'Return']]
        comparison = comparison.merge(reference, on=keys, suffixes=('', '_'+profile), validate='many_to_one')
        comparison['CAGRDelta_'+profile] = comparison.CAGR-comparison['CAGR_'+profile]
        comparison['MDDDelta_'+profile] = comparison.MDD-comparison['MDD_'+profile]
        comparison['RelativeFinalWealth_'+profile] = (1+comparison.Return)/(1+comparison['Return_'+profile])-1
    comparison.to_csv(research_result_path('qqq_adaptive_comparison.csv'), index=False)
    windows, annual, transitions, interactions, verdicts = [], [], [], [], []
    equal_current = []
    for scenario, period in cases:
        profiles = list(LOCK['profiles']) if scenario == 'MAIN' else LOCK['stress_profiles']+['REENTRY_SELL', 'REENTRY_BUY']
        for number in LOCK['sources']:
            paths = {p: daily(scenario, period, number, p) for p in profiles}
            for profile, h in paths.items():
                # These source guard states depend on market data, not the modified trend confirmation.
                for column in ('Credit', 'Deep', 'Financial', 'Structural'):
                    pd.testing.assert_series_equal(h[column], paths['BASE'][column])
                if (h.Target < -1e-12).any() or (h.Target > 1+1e-12).any():
                    raise AssertionError('Invalid target')
                for date, row in h.loc[h.Accelerated.notna()].iterrows():
                    transitions.append({'Scenario': scenario, 'Period': period, 'Source': number, 'Profile': profile,
                                        'Date': date, 'Transition': row.Accelerated, 'SourceTarget': row.SourceTarget,
                                        'Target': row.Target, 'ReferenceTarget': paths['REENTRY'].loc[date, 'Target']})
            # Log wealth makes the 2x2 interaction exact even under compounding.
            effect = (np.log(paths['COMBINED'].Portfolio.iloc[-1]/paths['REENTRY'].Portfolio.iloc[-1])-
                      np.log(paths['SPEED'].Portfolio.iloc[-1]/paths['BASE'].Portfolio.iloc[-1]))
            interactions.append({'Scenario': scenario, 'Period': period, 'Source': number,
                                 'LogWealthInteraction': effect, 'InteractionFactorMinusOne': np.expm1(effect)})
            if scenario != 'MAIN' or period != 'CURRENT':
                continue
            for profile, h in paths.items():
                for reference in ('BASE', 'REENTRY'):
                    b = paths[reference]
                    for start, end in LOCK['incremental_gate']['subperiods']:
                        a_stats, b_stats = window_stats(h.Portfolio.loc[start:end]), window_stats(b.Portfolio.loc[start:end])
                        windows.append({'Source': number, 'Profile': profile, 'Reference': reference,
                                        'Start': start, 'End': end, **a_stats,
                                        'CAGRDelta': a_stats['CAGR']-b_stats['CAGR'],
                                        'MDDDelta': a_stats['MDD']-b_stats['MDD']})
                    ratio = h.Portfolio/b.Portfolio
                    step = (ratio/ratio.shift(1)).fillna(1.)
                    factors = step.groupby(step.index.year).prod()
                    np.testing.assert_allclose(factors.prod(), ratio.iloc[-1]/ratio.iloc[0], rtol=1e-10, atol=1e-10)
                    for year, factor in factors.items():
                        annual.append({'Source': number, 'Profile': profile, 'Reference': reference, 'Year': year,
                                       'RelativeWealthContribution': factor-1,
                                       'RelativeWealthWithoutYearAttribution': ratio.iloc[-1]/factor-1})
        if period == 'CURRENT':
            for profile in profiles:
                a, b = daily(scenario, period, 30, profile), daily(scenario, period, 32, profile)
                columns = ['Portfolio', 'Target', 'Active', 'Trend']
                equal_current.append({'Scenario': scenario, 'Profile': profile,
                                      'Identical': a[columns].equals(b[columns])})
    for number in LOCK['sources']:
        for profile in ('COMBINED', 'REENTRY_SELL', 'REENTRY_BUY'):
            selected = comparison.loc[(comparison.Source == number) & (comparison.Profile == profile)]
            main_row = selected.loc[(selected.Scenario == 'MAIN') & (selected.Period == 'CURRENT')].iloc[0]
            stress = selected.loc[selected.Scenario != 'MAIN']
            proxies = selected.loc[selected.Period != 'CURRENT']
            gate = LOCK['incremental_gate']
            passed = (main_row.CAGRDelta_REENTRY > 1e-10 and
                      main_row.MDDDelta_REENTRY >= -gate['current_mdd_deterioration_max']-1e-10 and
                      stress.CAGRDelta_REENTRY.min() > 1e-10 and
                      proxies.MDDDelta_REENTRY.min() >= -gate['each_proxy_mdd_deterioration_max']-1e-10)
            verdicts.append({'Source': number, 'Profile': profile, 'MainCAGRDelta': main_row.CAGRDelta_REENTRY,
                             'MainMDDDelta': main_row.MDDDelta_REENTRY,
                             'WorstStressCAGRDelta': stress.CAGRDelta_REENTRY.min(),
                             'WorstProxyMDDDelta': proxies.MDDDelta_REENTRY.min(), 'IncrementalGatePass': bool(passed)})
    for name, records in [('windows', windows), ('annual_contributions', annual), ('transitions', transitions),
                           ('interactions', interactions), ('verdict', verdicts)]:
        pd.DataFrame(records).to_csv(research_result_path('qqq_adaptive_'+name+'.csv'), index=False)
    # Trace the changed valuation memory, which explains why earlier defense
    # can remove a later defense even with unchanged credit/deep guard rules.
    baseline = daily('MAIN', 'CURRENT', 30, 'REENTRY')
    combined = daily('MAIN', 'CURRENT', 30, 'COMBINED')
    columns = ['ValuationPeak', 'Defense', 'Target', 'QQQActual', 'Portfolio']
    trace = baseline[columns].add_prefix('REENTRY_').join(combined[columns].add_prefix('COMBINED_'))
    trace.loc['2019-10-01':'2020-04-14'].to_csv(research_result_path('qqq_adaptive_valuation_memory_trace.csv'))
    outputs = sorted(research_result_path('qqq_adaptive_evaluation.json').parent.glob('qqq_adaptive_*.csv'))
    record = {'RunCount': len(summary), 'PrimaryRuns': len(summary)-len(supplement),
              'SupplementalDirectionalStressRuns': len(supplement), 'InputHashReferencesVerified': verified,
              'PriorRunnerParityChecks': sum(c.get('PriorRunnerParity', False) for c in checks),
              'PrefixChecks': sum(c.get('PrefixPassed', False) for c in checks),
              'FeaturePrefixChecks': sum(c.get('FeaturePrefix', False) for c in checks),
              'ProtectedGuardStatesEqualAcrossProfiles': True, 'CurrentSourceComparisons': equal_current,
              'EvaluationSHA256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'OutputSHA256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs}}
    research_result_path('qqq_adaptive_evaluation.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(pd.DataFrame(verdicts).to_string(index=False))
    print(comparison.loc[(comparison.Source == 30) & (comparison.Period == 'CURRENT'),
          ['Scenario', 'Profile', 'CAGR', 'MDD', 'TradeDates', 'CAGRDelta_REENTRY']].to_string(index=False))
    print({k: v for k, v in record.items() if k not in ('OutputSHA256', 'CurrentSourceComparisons')})


if __name__ == '__main__':
    main()
