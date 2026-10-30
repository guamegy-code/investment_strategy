"""Fixed-screen evaluation of guard-preserving QQQ reentry paths."""
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
from qqq_guarded_reentry import LOCK, LOCK_PATH
from qqq_action_attribution import episodes


def daily(scenario, period, source, profile):
    return pd.read_csv(research_result_path(f'qqq_guarded_{scenario.lower()}_{period.lower()}_{source}_{profile.lower()}_daily.csv'),
                       index_col='Date', parse_dates=True)


def cagr(series):
    years = (series.index[-1]-series.index[0]).days/365.25
    return float((series.iloc[-1]/series.iloc[0])**(1/years)-1)


def main():
    expected = {'MAIN': 24, 'COST3': 8, 'DELAY1': 8}
    frames = []
    for scenario, size in expected.items():
        frame = pd.read_csv(research_result_path(f'qqq_guarded_{scenario.lower()}_summary.csv'))
        if len(frame) != size or frame.MaxGuardIncrement.abs().max() > 1e-12:
            raise AssertionError(f'Incomplete matrix/guard violation: {scenario}')
        frames.append(frame)
    summary = pd.concat(frames, ignore_index=True)
    key = ['Period', 'Source', 'Scenario']
    base = summary.loc[summary.Profile == 'BASE', key+['CAGR', 'MDD', 'TradeDates']]
    paired = summary.merge(base, on=key, suffixes=('', '_BASE'), validate='many_to_one')
    paired['CAGRDelta'] = paired.CAGR-paired.CAGR_BASE
    paired['MDDDelta'] = paired.MDD-paired.MDD_BASE
    paired.to_csv(research_result_path('qqq_guarded_comparison.csv'), index=False)
    windows, entries, verdicts, clusters = [], [], [], []
    for source in LOCK['sources']:
        b = daily('MAIN', 'CURRENT', source, 'BASE').Portfolio
        labels = pd.read_csv(research_result_path(f'qqq_guarded_main_current_{source}_labels.csv'))
        clusters.append({'Source': source, 'EligibleLabels': len(labels), 'Episodes': labels.Episode.nunique()})
        for profile in LOCK['profiles']:
            h = daily('MAIN', 'CURRENT', source, profile)
            for start, end in LOCK['followup_gate']['subperiods']:
                values, reference = h.Portfolio.loc[start:end], b.loc[start:end]
                windows.append({'Source': source, 'Profile': profile, 'Start': start, 'End': end,
                                'CAGR': cagr(values), 'CAGRDelta': cagr(values)-cagr(reference)})
            active, begin = h.Active.astype(bool), None
            for i, flag in enumerate(active):
                if flag and begin is None:
                    begin = i
                elif not flag and begin is not None:
                    end_i = min(i+1, len(h)-1)
                    ratio = h.Portfolio/b
                    entries.append({'Source': source, 'Profile': profile,
                                    'EntryDecision': h.index[begin], 'ExitDecision': h.index[i],
                                    'LastExecutionMark': h.index[end_i], 'ActiveDays': i-begin,
                                    'RelativeWealthChange': ratio.iloc[end_i]/ratio.iloc[begin]-1})
                    begin = None
            if begin is not None:
                raise AssertionError('Unclosed entry needs explicit censor handling')
    pd.DataFrame(windows).to_csv(research_result_path('qqq_guarded_windows.csv'), index=False)
    entries = pd.DataFrame(entries)
    if len(entries):
        event_map = {number: episodes(daily('MAIN', 'CURRENT', number, 'BASE').SourceTarget) for number in LOCK['sources']}
        entries['Episode'] = entries.apply(lambda row: next(e['Episode'] for e in event_map[row.Source]
            if e['StartDecision'] <= row.EntryDecision <= e['EndDecision']), axis=1)
    entries.to_csv(research_result_path('qqq_guarded_entries.csv'), index=False)
    contributions = []
    for (number, profile), group in entries.groupby(['Source', 'Profile']):
        h, b = daily('MAIN', 'CURRENT', number, profile).Portfolio, daily('MAIN', 'CURRENT', number, 'BASE').Portfolio
        total_relative = h.iloc[-1]/b.iloc[-1]
        factors = []
        for event, sample in group.groupby('Episode'):
            # Cancellation resets target weights and can leave a different
            # within-episode drift after Active becomes false. Attribute through
            # the full source episode and its final full-stock execution.
            context = next(e for e in event_map[number] if e['Episode'] == event)
            start_i, end_i = context['StartIndex'], min(context['EndIndex']+1, len(h)-1)
            ratio = h/b
            factor = float(ratio.iloc[end_i]/ratio.iloc[start_i])
            factors.append(factor)
            contributions.append({'Source': number, 'Profile': profile, 'Episode': event, 'Entries': len(sample),
                                   'RelativeWealthContribution': factor-1,
                                   'RelativeWealthWithoutThisEpisode': total_relative/factor-1})
        np.testing.assert_allclose(np.prod(factors), total_relative, rtol=1e-10, atol=1e-10)
    pd.DataFrame(contributions).to_csv(research_result_path('qqq_guarded_episode_contributions.csv'), index=False)
    windows = pd.DataFrame(windows)
    gate = LOCK['followup_gate']
    for source in LOCK['sources']:
        for profile in LOCK['profiles'][1:]:
            selected = paired.loc[(paired.Source == source) & (paired.Profile == profile)]
            current = selected.loc[(selected.Period == 'CURRENT') & (selected.Scenario == 'MAIN')].iloc[0]
            cost = selected.loc[(selected.Period == 'CURRENT') & (selected.Scenario == 'COST3')].iloc[0]
            delay = selected.loc[(selected.Period == 'CURRENT') & (selected.Scenario == 'DELAY1')].iloc[0]
            proxies = selected.loc[selected.Period != 'CURRENT']
            wins = int((windows.loc[(windows.Source == source) & (windows.Profile == profile), 'CAGRDelta'] > 1e-10).sum())
            passed = (current.CAGRDelta > 1e-10 and current.MDDDelta >= -gate['main_mdd_deterioration_max']-1e-10 and
                      wins >= gate['positive_subperiods_min'] and cost.CAGRDelta > 1e-10 and delay.CAGRDelta > 1e-10 and
                      proxies.MDDDelta.min() >= -gate['proxy_mdd_deterioration_max']-1e-10)
            verdicts.append({'Source': source, 'Profile': profile, 'CAGRDelta': current.CAGRDelta,
                             'MDDDelta': current.MDDDelta, 'PositiveSubperiods': wins,
                             'Cost3CAGRDelta': cost.CAGRDelta, 'Delay1CAGRDelta': delay.CAGRDelta,
                             'WorstProxyMDDDelta': proxies.MDDDelta.min(), 'HistoricalFollowupPass': bool(passed),
                             'ModelPredictions': int(current.Predictions),
                             'ModelEvaluable': profile != 'MODEL10' or current.Predictions > 0})
    pd.DataFrame(verdicts).to_csv(research_result_path('qqq_guarded_verdict.csv'), index=False)
    checks = set()
    verified = 0
    for scenario in expected:
        for period in (LOCK['periods'] if scenario == 'MAIN' else ['CURRENT']):
            path = research_result_path(f'qqq_guarded_{scenario.lower()}_{period.lower()}_manifest.json')
            manifest = json.loads(path.read_text(encoding='utf-8'))
            if manifest['Design'] != LOCK:
                raise AssertionError('Design changed')
            for name, digest in manifest['InputSHA256'].items():
                if hashlib.sha256((PROJECT_ROOT/name).read_bytes()).hexdigest() != digest:
                    raise AssertionError(f'Changed input: {name}')
                verified += 1
            for check in manifest['Checks']:
                checks.add((scenario, json.dumps(check, sort_keys=True)))
    for scenario in expected:
        for profile in LOCK['profiles']:
            a, b = daily(scenario, 'CURRENT', 30, profile), daily(scenario, 'CURRENT', 32, profile)
            pd.testing.assert_frame_equal(a[['Portfolio', 'Target', 'Active']], b[['Portfolio', 'Target', 'Active']])
    static = pd.read_csv(research_result_path('qqq_guarded_static_summary.csv'))
    if len(static) != 6 or static.MaxGuardIncrement.abs().max() > 1e-12:
        raise AssertionError('Incomplete static-size diagnostic')
    timed = paired.loc[(paired.Source == 30) & (paired.Period == 'CURRENT') &
                       paired.Profile.isin(['RULE10', 'RULE_HALF'])]
    diagnostic = timed.merge(static[['Scenario', 'Profile', 'CAGR', 'MDD', 'TradeDates']],
                             on=['Scenario', 'Profile'], suffixes=('', '_Static'), validate='one_to_one')
    diagnostic['TimingCAGRDelta'] = diagnostic.CAGR-diagnostic.CAGR_Static
    diagnostic['TimingMDDDelta'] = diagnostic.MDD-diagnostic.MDD_Static
    diagnostic.to_csv(research_result_path('qqq_guarded_static_comparison.csv'), index=False)
    static_manifest = json.loads(research_result_path('qqq_guarded_static_manifest.json').read_text(encoding='utf-8'))
    main_prepared = json.loads(research_result_path('qqq_guarded_main_current_manifest.json').read_text(encoding='utf-8'))['PreparedSHA256']
    if static_manifest['PreparedSHA256'] != main_prepared:
        raise AssertionError('Diagnostic used different prepared prices')
    for name, digest in static_manifest['InputSHA256'].items():
        if hashlib.sha256((PROJECT_ROOT/name).read_bytes()).hexdigest() != digest:
            raise AssertionError(f'Changed diagnostic input: {name}')
        verified += 1
    directory = research_result_path('qqq_guarded_evaluation.json').parent
    outputs = sorted(directory.glob('qqq_guarded_*.csv'))
    record = {'RunCount': len(summary), 'PostHocDiagnosticRuns': len(static), 'InputHashReferencesVerified': verified,
              'NativeBaselineParityChecks': sum('NativeBaselineParity' in c[1] for c in checks),
              'PrefixChecks': sum('PrefixPassed' in c[1] for c in checks),
              'Current30And32DailyPathsIdentical': True, 'LabelClusters': clusters,
              'EvaluationCodeSHA256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'OutputSHA256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs}}
    research_result_path('qqq_guarded_evaluation.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(pd.DataFrame(verdicts).to_string(index=False))
    print({k: v for k, v in record.items() if k not in ('OutputSHA256', 'EvaluationCodeSHA256')})


if __name__ == '__main__':
    main()
