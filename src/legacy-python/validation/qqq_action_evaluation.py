"""Episode-weighted, descriptive summaries; never select a hindsight policy."""
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PROJECT_ROOT
from result_paths import research_result_path


def summarize(frame, weekly):
    frame = frame.loc[frame.Complete & frame.Policy.ne('FOLLOW_BASE')].copy()
    keys = ['Period', 'Source', 'ExecutionDate', 'Kind', 'Horizon']
    base_path = research_result_path('qqq_actions_all_weekly.csv' if weekly else 'qqq_actions_all_labels.csv')
    base = pd.read_csv(base_path)
    base = base.loc[base.Complete & base.Policy.eq('FOLLOW_BASE'), keys+['MDD']]
    frame = frame.merge(base, on=keys, suffixes=('', '_Base'), validate='many_to_one')
    frame['MDDDelta'] = frame.MDD-frame.MDD_Base
    frame['Positive'] = frame.DeltaToBase > 1e-10
    frame['ReentryIsHardGuarded'] = frame.CreditGuard.eq('TRUE') | frame.DeepGuard.eq('TRUE')
    group = ['Period', 'Source', 'Kind', 'Horizon', 'Policy']
    result = []
    for key, sample in frame.groupby(group):
        by_event = sample.groupby('Episode').DeltaToBase.mean()
        result.append(dict(zip(group, key)) | {'Labels': len(sample), 'Episodes': sample.Episode.nunique(),
            'PositiveLabels': int(sample.Positive.sum()), 'MeanDelta': sample.DeltaToBase.mean(),
            'MedianDelta': sample.DeltaToBase.median(), 'MinDelta': sample.DeltaToBase.min(),
            'MaxDelta': sample.DeltaToBase.max(), 'EqualEpisodeMeanDelta': by_event.mean(),
            'PositiveEpisodeMeans': int((by_event > 1e-10).sum()),
            'MeanMDDDelta': sample.MDDDelta.mean(), 'WorstMDDDelta': sample.MDDDelta.min()})
    return pd.DataFrame(result), frame


def main():
    labels = pd.read_csv(research_result_path('qqq_actions_all_labels.csv'))
    weekly = pd.read_csv(research_result_path('qqq_actions_all_weekly.csv'))
    action_summary, detailed = summarize(labels, False)
    weekly_summary, weekly_detail = summarize(weekly, True)
    action_summary.to_csv(research_result_path('qqq_actions_label_summary.csv'), index=False)
    weekly_summary.to_csv(research_result_path('qqq_actions_weekly_summary.csv'), index=False)
    # Show differences between event families without selecting a favorable
    # subset as a strategy. The episode label is only descriptive metadata.
    group = ['Period', 'Source', 'Episode', 'Horizon', 'Policy']
    weekly_detail.groupby(group).agg(Labels=('DeltaToBase', 'size'), MeanDelta=('DeltaToBase', 'mean'),
        MedianDelta=('DeltaToBase', 'median'), WorstDelta=('DeltaToBase', 'min'),
        MeanMDDDelta=('MDDDelta', 'mean')).reset_index().to_csv(
            research_result_path('qqq_actions_weekly_by_episode.csv'), index=False)
    # Labels for the same CURRENT source path must not be counted twice.
    for frame in (labels, weekly):
        a = frame.loc[(frame.Period == 'CURRENT') & (frame.Source == 30)].drop(columns='Source').reset_index(drop=True)
        b = frame.loc[(frame.Period == 'CURRENT') & (frame.Source == 32)].drop(columns='Source').reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b)
    manifest = json.loads(research_result_path('qqq_actions_manifest.json').read_text(encoding='utf-8'))
    for path, digest in manifest['InputSHA256'].items():
        if hashlib.sha256((PROJECT_ROOT/path).read_bytes()).hexdigest() != digest:
            raise AssertionError(f'Changed experiment input: {path}')
    events = pd.read_csv(research_result_path('qqq_actions_all_episodes.csv'))
    counts = []
    for (period, number), frame in events.groupby(['Period', 'Source']):
        counts.append({'Period': period, 'Source': int(number), 'Episodes': len(frame),
                       'PositiveBaseVsHold': int((frame.BaseMinusHold > 1e-10).sum()),
                       'NegativeBaseVsHold': int((frame.BaseMinusHold < -1e-10).sum())})
    directory = research_result_path('qqq_actions_evaluation.json').parent
    outputs = [p for p in directory.glob('qqq_actions_*.csv')]
    record = {'EpisodeOutcomes': counts, 'InputHashesVerified': len(manifest['InputSHA256']),
              'Current30And32LabelsIdentical': True, 'UniqueCurrentEpisodeCount': 8,
              'EvaluationCodeSHA256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'OutputSHA256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs}}
    research_result_path('qqq_actions_evaluation.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(pd.DataFrame(counts).to_string(index=False))
    print('\nActual action comparisons, current Strategy 30:')
    print(action_summary.loc[(action_summary.Period == 'CURRENT') & (action_summary.Source == 30)].to_string(index=False))
    print('\nWeekly reentry comparisons, each episode equally weighted:')
    print(weekly_summary.loc[(weekly_summary.Source == 30) & (weekly_summary.Policy == 'HALF_REENTRY'),
          ['Period', 'Horizon', 'Labels', 'Episodes', 'EqualEpisodeMeanDelta', 'PositiveEpisodeMeans', 'WorstMDDDelta']].to_string(index=False))


if __name__ == '__main__':
    main()
