"""Summarize the fixed risk-allocation experiment without parameter selection."""
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from result_paths import research_result_path
from config import PROJECT_ROOT
from pension_risk_allocation_review import LOCK


STEMS = ['pension_risk_base30', 'pension_risk_long30', 'pension_risk_base32',
         'pension_risk_cost3', 'pension_risk_delay1', 'pension_risk_idio_slow', 'pension_risk_idio_v']


def verify_evidence():
    directory = research_result_path('pension_risk_verification.json').parent
    checks, manifests = set(), list(directory.glob('pension_risk_*_manifest.json'))
    verified_inputs = 0
    for path in manifests:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        for name, digest in manifest['InputsSHA256'].items():
            if hashlib.sha256((PROJECT_ROOT/name).read_bytes()).hexdigest() != digest:
                raise AssertionError(f'Input changed after experiment: {name}')
            verified_inputs += 1
        for check in manifest['PrefixChecks']:
            if not check['Passed']:
                raise AssertionError('Failed prefix check')
            checks.add(tuple(check[k] for k in ['Case', 'Source', 'Profile', 'CostMultiple',
                                               'ExtraDelay', 'ConditionalStress', 'Cutoff']))
    if len(checks) != 16:
        raise AssertionError('Incomplete real-data prefix checks')
    for case in ['long_xlk', 'dotcom_xlk']:
        for profile in ['risk_balance', 'cppi_cap']:
            repeated = pd.read_csv(directory/f'pension_risk_prefix_{case}_{profile}_daily.csv')
            original = pd.read_csv(directory/f'pension_risk_long30_{case}_{profile}_daily.csv')
            pd.testing.assert_frame_equal(repeated, original)
    outputs = sorted(directory.glob('pension_risk_*.csv'))+sorted(directory.glob('pension_risk_*_manifest.json'))
    record = {'VerifiedInputReferences': verified_inputs, 'ManifestCount': len(manifests),
              'UniquePrefixChecksPassed': len(checks), 'RepeatedDailyPathsIdentical': 4,
              'OutputSHA256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs},
              'EvaluationSHA256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (directory/'pension_risk_verification.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print({k: v for k, v in record.items() if k not in ('OutputSHA256', 'EvaluationSHA256')})


def main():
    frames = []
    for stem in STEMS:
        frame = pd.read_csv(research_result_path(stem+'_summary.csv'))
        frame['Run'] = stem
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True)
    keys = ['Case', 'Source', 'Currency', 'CostMultiple', 'ExtraDelay', 'ConditionalStress', 'Run']
    bases = combined[combined.Profile == 'BASE'][keys+['CAGR', 'MDD', 'Return', 'TradeDates']]
    paired = combined.merge(bases, on=keys, suffixes=('', '_BASE'), validate='many_to_one')
    for metric in ['CAGR', 'MDD', 'Return', 'TradeDates']:
        paired['Delta'+metric] = paired[metric]-paired[metric+'_BASE']
    paired.to_csv(research_result_path('pension_risk_comparison.csv'), index=False)
    long = paired[paired.Run == 'pension_risk_long30']
    long = long[long.Case.str.startswith('LONG_')]
    if len(long) != 18 or len(combined) != 84:
        raise AssertionError('Incomplete fixed experiment matrix')
    gate = LOCK['historical_followup_screen']
    verdicts = []
    for profile in ['MONTHLY50', 'RISK_BALANCE', 'CPPI_CAP', 'CPPI_ONLY']:
        selected = long[long.Profile == profile]
        wins = int((selected.DeltaCAGR > 0).sum())
        worst_dd = float(selected.DeltaMDD.min())
        passed = wins >= gate['long_positive_cagr_cases_min'] and worst_dd >= -gate['long_mdd_deterioration_max']
        verdicts.append({'Profile': profile, 'PositiveCAGRCases': wins,
                         'MinCAGRDelta': float(selected.DeltaCAGR.min()),
                         'MaxCAGRDelta': float(selected.DeltaCAGR.max()),
                         'WorstMDDDelta': worst_dd, 'Pass': passed})
    research_result_path('pension_risk_verdict.json').write_text(json.dumps(verdicts, indent=2), encoding='utf-8')
    # A conditional shock may have a lower full-period MDD than a separate real
    # market crash. Report its local window and same-date counterfactual as well.
    stress_rows = []
    for scenario in ['IDIO_SLOW', 'IDIO_V']:
        stem = 'pension_risk_'+scenario.lower()
        for profile in LOCK['profiles']:
            name = profile.lower()
            shocked = pd.read_csv(research_result_path(f'{stem}_domestic_{name}_daily.csv'), index_col='Date', parse_dates=True)
            actual = pd.read_csv(research_result_path(f'pension_risk_base30_domestic_{name}_daily.csv'), index_col='Date', parse_dates=True)
            start = shocked.index.searchsorted(LOCK['conditional_stress']['start'])
            end = min(start+70, len(shocked)-1)
            window = shocked.Portfolio.iloc[start-1:end+1]
            stress_rows.append({'Scenario': scenario, 'Profile': profile,
                                'WindowStart': str(window.index[0].date()), 'WindowEnd': str(window.index[-1].date()),
                                'WindowMDD': float((window/window.cummax()-1).min()),
                                'WindowReturn': float(window.iloc[-1]/window.iloc[0]-1),
                                'EndWealthVsUnshocked': float(shocked.Portfolio.iloc[end]/actual.Portfolio.iloc[end]-1),
                                'FinalWealthVsUnshocked': float(shocked.Portfolio.iloc[-1]/actual.Portfolio.iloc[-1]-1)})
    pd.DataFrame(stress_rows).to_csv(research_result_path('pension_risk_conditional_windows.csv'), index=False)
    print(pd.DataFrame(verdicts).to_string(index=False))
    print('Completed comparisons:', len(combined))
    verify_evidence()


if __name__ == '__main__':
    main()
