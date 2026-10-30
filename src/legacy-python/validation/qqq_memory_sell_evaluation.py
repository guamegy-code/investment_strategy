"""Evaluate the fixed memory-preserving sell matrix against the current best."""
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qqq_memory_sell as study
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
        case = f'qqq_memory_{scenario.lower()}_{period.lower()}'
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
    reference = summary.loc[summary.Profile.eq('REFERENCE'), keys+['CAGR', 'MDD', 'Return']]
    comparison = summary.merge(reference, on=keys, suffixes=('', '_REFERENCE'), validate='many_to_one')
    comparison['CAGRDelta'] = comparison.CAGR-comparison.CAGR_REFERENCE
    comparison['MDDDelta'] = comparison.MDD-comparison.MDD_REFERENCE
    comparison['RelativeFinalWealth'] = (1+comparison.Return)/(1+comparison.Return_REFERENCE)-1
    comparison.to_csv(research_result_path('qqq_memory_comparison.csv'), index=False)
    windows, annual, events, interactions, controls, verdicts, equal_current = [], [], [], [], [], [], []
    for scenario, period in cases:
        for number in LOCK['sources']:
            paths = {p: daily(scenario, period, number, p) for p in LOCK['profiles']}
            base = paths['REFERENCE']
            old = study.archived(scenario, period, number, 'COMBINED')
            old_stats = window_stats(old.Portfolio)
            for profile, h in paths.items():
                pd.testing.assert_frame_equal(h[study.MEMORY_COLUMNS], base[study.MEMORY_COLUMNS], check_exact=True)
                if (h.Target < -1e-12).any() or (h.Target > 1+1e-12).any():
                    raise AssertionError('Invalid target')
                if ((h.Target-h.SourceTarget).loc[h.Blocked] > 1e-12).any():
                    raise AssertionError('Protected target increased')
                if (h.EarlySell & h.Active).any():
                    raise AssertionError('Early sell and reentry active together')
                stats = window_stats(h.Portfolio)
                controls.append({'Scenario': scenario, 'Period': period, 'Source': number, 'Profile': profile,
                                 'OldCombinedCAGR': old_stats['CAGR'], 'OldCombinedMDD': old_stats['MDD'],
                                 'CAGRDeltaToOldCombined': stats['CAGR']-old_stats['CAGR'],
                                 'MDDDeltaToOldCombined': stats['MDD']-old_stats['MDD']})
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
                ratio = h.Portfolio/base.Portfolio
                factors = (ratio/ratio.shift(1)).fillna(1.).groupby(ratio.index.year).prod()
                np.testing.assert_allclose(factors.prod(), ratio.iloc[-1]/ratio.iloc[0], rtol=1e-10, atol=1e-10)
                for year, factor in factors.items():
                    annual.append({'Source': number, 'Profile': profile, 'Year': year,
                                   'RelativeWealthContribution': factor-1,
                                   'RelativeWealthWithoutYearAttribution': ratio.iloc[-1]/factor-1})
            # Exact compounded 2x2 interaction; this includes subsequent state/holding paths.
            effect = (np.log(paths['MEMORY_BOTH'].Portfolio.iloc[-1]/paths['MEMORY_TREND'].Portfolio.iloc[-1])-
                      np.log(paths['MEMORY_VAL'].Portfolio.iloc[-1]/base.Portfolio.iloc[-1]))
            interactions.append({'Scenario': scenario, 'Period': period, 'Source': number,
                                 'LogWealthInteraction': effect, 'InteractionFactorMinusOne': np.expm1(effect)})
        if period == 'CURRENT':
            for profile in LOCK['profiles']:
                a, b = daily(scenario, period, 30, profile), daily(scenario, period, 32, profile)
                columns = ['Portfolio', 'Target', 'Active', 'EarlySell', 'Trend', 'Defense', 'ValuationPeak']
                equal_current.append({'Scenario': scenario, 'Profile': profile, 'Identical': a[columns].equals(b[columns])})
    for number in LOCK['sources']:
        for profile in list(LOCK['profiles'])[1:]:
            selected = comparison.loc[(comparison.Source == number) & (comparison.Profile == profile)]
            main_row = selected.loc[(selected.Scenario == 'MAIN') & (selected.Period == 'CURRENT')].iloc[0]
            stress = selected.loc[selected.Scenario != 'MAIN']
            proxies = selected.loc[selected.Period != 'CURRENT']
            gate = LOCK['gate']
            passed = (main_row.CAGRDelta > 1e-10 and
                      main_row.MDDDelta >= -gate['current_mdd_deterioration_max']-1e-10 and
                      stress.CAGRDelta.min() > 1e-10 and
                      proxies.MDDDelta.min() >= -gate['each_proxy_mdd_deterioration_max']-1e-10)
            verdicts.append({'Source': number, 'Profile': profile, 'MainCAGRDelta': main_row.CAGRDelta,
                             'MainMDDDelta': main_row.MDDDelta, 'WorstStressCAGRDelta': stress.CAGRDelta.min(),
                             'WorstProxyMDDDelta': proxies.MDDDelta.min(), 'IncrementalGatePass': bool(passed)})
    for name, records in [('windows', windows), ('annual_contributions', annual), ('events', events),
                           ('interactions', interactions), ('old_combined_comparison', controls), ('verdict', verdicts)]:
        pd.DataFrame(records).to_csv(research_result_path('qqq_memory_'+name+'.csv'), index=False)
    columns = ['ValuationPeak', 'Defense', 'Target', 'QQQActual', 'Portfolio']
    trace = pd.concat([daily('MAIN', 'CURRENT', 30, p)[columns].add_prefix(p+'_') for p in LOCK['profiles']], axis=1)
    trace = trace.join(study.archived('MAIN', 'CURRENT', 30, 'COMBINED')[columns].add_prefix('OLD_COMBINED_'))
    trace.loc['2019-10-01':'2020-04-14'].to_csv(research_result_path('qqq_memory_valuation_trace.csv'))
    record = {'RunCount': len(summary), 'InputHashReferencesVerified': verified,
              'ArchivedReferenceParityChecks': sum(c.get('ArchivedReferenceParity', False) for c in checks),
              'PrefixChecks': sum(c.get('PrefixPassed', False) for c in checks),
              'FeaturePrefixChecks': sum(c.get('FeaturePrefix', False) for c in checks),
              'ValuationAndGuardsPreservedChecks': sum(c.get('ValuationAndGuardsPreserved', False) for c in checks),
              'CurrentSourceComparisons': equal_current,
              'EvaluationSHA256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'ManifestSHA256': manifests}
    for key, expected in [('RunCount', 48), ('ArchivedReferenceParityChecks', 12), ('PrefixChecks', 24),
                          ('FeaturePrefixChecks', 6), ('ValuationAndGuardsPreservedChecks', 48)]:
        if record[key] != expected:
            raise AssertionError(f'Incomplete checks: {key}={record[key]}')
    outputs = sorted(research_result_path('qqq_memory_evaluation.json').parent.glob('qqq_memory_*.csv'))
    record['OutputSHA256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs}
    research_result_path('qqq_memory_evaluation.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(pd.DataFrame(verdicts).to_string(index=False))
    print(comparison.loc[(comparison.Source == 30) & (comparison.Period == 'CURRENT'),
          ['Scenario', 'Profile', 'CAGR', 'MDD', 'TradeDates', 'CAGRDelta', 'RelativeFinalWealth']].to_string(index=False))
    print({k: v for k, v in record.items() if k not in ('OutputSHA256', 'ManifestSHA256', 'CurrentSourceComparisons')})


if __name__ == '__main__':
    main()
