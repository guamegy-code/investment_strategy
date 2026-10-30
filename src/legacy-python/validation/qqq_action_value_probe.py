"""Chronological, episode-balanced action-value forecast feasibility probe."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import DATA_DIR, PROJECT_ROOT
from downloader import _align_macro_to_qqq_sessions, _load_fred_series
from result_paths import research_result_path

LOCK_PATH = PROJECT_ROOT/'validation/qqq_action_value_probe_lock_20261026.json'
LOCK = json.loads(LOCK_PATH.read_text(encoding='utf-8'))
FEATURES = LOCK['features']


def features():
    q = pd.read_csv(DATA_DIR/'QQQ.csv', index_col='Date', parse_dates=True).Close
    s = pd.read_csv(DATA_DIR/'SPY.csv', index_col='Date', parse_dates=True).Close.reindex(q.index)
    b = _load_fred_series(PROJECT_ROOT/'tmp/BAA10Y.csv')
    b = _align_macro_to_qqq_sessions(b, q.index, lag_sessions=1)
    return pd.DataFrame({FEATURES[0]: q/q.ewm(span=200, adjust=False, min_periods=200).mean()-1,
                         FEATURES[1]: (q/q.shift(20))/(s/s.shift(20))-1,
                         FEATURES[2]: b-b.shift(20)}, index=q.index)


def weighted_ridge(train, current, alpha):
    sizes = train.groupby('Episode').Episode.transform('size')
    weights = (1/sizes).to_numpy()
    x, y = train[FEATURES].to_numpy(), train.DeltaToBase.to_numpy()
    center = np.average(x, weights=weights, axis=0)
    scale = np.sqrt(np.average((x-center)**2, weights=weights, axis=0))
    scale[scale < 1e-10] = 1.
    z = np.column_stack([np.ones(len(x)), (x-center)/scale])
    penalty = np.diag([0., *([alpha]*len(FEATURES))])
    beta = np.linalg.solve(z.T@(weights[:, None]*z)+penalty, z.T@(weights*y))
    estimate = np.r_[1., (current[FEATURES].to_numpy(dtype=float)-center)/scale]@beta
    return float(estimate), float(np.average(y, weights=weights))


def walk_forward(dataset):
    rows = []
    for horizon, sample in dataset.groupby('Horizon'):
        sample = sample.sort_values('DecisionDate')
        for _, current in sample.iterrows():
            train = sample.loc[(sample.EndDate < current.DecisionDate) &
                               (sample.EpisodeEndDecision < current.DecisionDate)]
            if len(train) < LOCK['min_training_rows'] or train.Episode.nunique() < LOCK['min_completed_training_episodes']:
                continue
            estimate, mean = weighted_ridge(train, current, LOCK['alpha'])
            rows.append({'DecisionDate': current.DecisionDate, 'ExecutionDate': current.ExecutionDate,
                         'EndDate': current.EndDate, 'Episode': current.Episode, 'Horizon': horizon,
                         'TrainingRows': len(train), 'TrainingEpisodes': train.Episode.nunique(),
                         'LastTrainingOutcome': train.EndDate.max(), 'LastTrainingEpisodeEnd': train.EpisodeEndDecision.max(),
                         'Prediction': estimate, 'ExpandingMean': mean, 'Outcome': current.DeltaToBase,
                         'Selected': estimate > 0, 'MeanSelected': mean > 0,
                         'IncrementalPayoff': current.DeltaToBase if estimate > 0 else 0.,
                         'MeanIncrementalPayoff': current.DeltaToBase if mean > 0 else 0.})
    return pd.DataFrame(rows)


def main():
    label_path = research_result_path('qqq_actions_all_weekly.csv')
    event_path = research_result_path('qqq_actions_all_episodes.csv')
    labels = pd.read_csv(label_path, parse_dates=['DecisionDate', 'ExecutionDate', 'EndDate'])
    labels = labels.loc[(labels.Period == 'CURRENT') & (labels.Source == 30) & labels.Complete &
                        (labels.Policy == LOCK['policy'])].copy()
    events = pd.read_csv(event_path, parse_dates=['EndDecision'])
    events = events.loc[(events.Period == 'CURRENT') & (events.Source == 30), ['Episode', 'EndDecision']]
    labels = labels.merge(events.rename(columns={'EndDecision': 'EpisodeEndDecision'}), on='Episode', validate='many_to_one')
    labels = labels.join(features(), on='DecisionDate').dropna(subset=FEATURES)
    predictions = walk_forward(labels)
    if predictions.empty:
        raise ValueError('No chronology-admissible predictions')
    assert (predictions.LastTrainingOutcome < predictions.DecisionDate).all()
    assert (predictions.LastTrainingEpisodeEnd < predictions.DecisionDate).all()
    checks = []
    for cutoff in ('2020-12-31', '2022-12-31', '2025-12-31'):
        prefix = walk_forward(labels.loc[labels.DecisionDate <= cutoff])
        full = predictions.loc[predictions.DecisionDate <= cutoff].reset_index(drop=True)
        pd.testing.assert_frame_equal(prefix.reset_index(drop=True), full)
        checks.append({'Cutoff': cutoff, 'PredictionPrefixIdentical': True})
    predictions.to_csv(research_result_path('qqq_action_value_predictions.csv'), index=False)
    labels.to_csv(research_result_path('qqq_action_value_dataset.csv'), index=False)
    summary = []
    for horizon, frame in predictions.groupby('Horizon'):
        by_episode = frame.groupby('Episode')[['IncrementalPayoff', 'MeanIncrementalPayoff', 'Outcome']].mean()
        omitted = [float(by_episode.drop(index=e).IncrementalPayoff.mean()) for e in by_episode.index]
        summary.append({'Horizon': horizon, 'Decisions': len(frame), 'TestEpisodes': frame.Episode.nunique(),
                        'Selected': int(frame.Selected.sum()), 'MeanControlSelected': int(frame.MeanSelected.sum()),
                        'RMSE': float(np.sqrt(((frame.Prediction-frame.Outcome)**2).mean())),
                        'MeanControlRMSE': float(np.sqrt(((frame.ExpandingMean-frame.Outcome)**2).mean())),
                        'EqualEpisodePayoff': by_episode.IncrementalPayoff.mean(),
                        'MeanControlEqualEpisodePayoff': by_episode.MeanIncrementalPayoff.mean(),
                        'AlwaysReentryEqualEpisodePayoff': by_episode.Outcome.mean(),
                        'PositiveTestEpisodes': int((by_episode.IncrementalPayoff > 1e-10).sum()),
                        'WorstLeaveOneEpisodeOutPayoff': min(omitted),
                        'Start': str(frame.DecisionDate.min().date()), 'End': str(frame.DecisionDate.max().date())})
    pd.DataFrame(summary).to_csv(research_result_path('qqq_action_value_summary.csv'), index=False)
    paths = [Path(__file__), LOCK_PATH, label_path, event_path, DATA_DIR/'QQQ.csv', DATA_DIR/'SPY.csv', PROJECT_ROOT/'tmp/BAA10Y.csv',
             PROJECT_ROOT/'src/legacy-python/downloader.py']
    manifest = {'Design': LOCK, 'Checks': checks, 'InputSHA256': {
        str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    research_result_path('qqq_action_value_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(pd.DataFrame(summary).to_string(index=False))


if __name__ == '__main__':
    main()
