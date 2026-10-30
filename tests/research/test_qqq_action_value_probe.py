"""No immature outcomes or active episodes enter the action-value fit."""
from tests import PROJECT_ROOT
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(PROJECT_ROOT/'src/legacy-python/validation'))
from qqq_action_value_probe import FEATURES, walk_forward, weighted_ridge


class QQQActionValueProbeTests(unittest.TestCase):
    def dataset(self):
        rows = []
        for episode in range(1, 7):
            for i, date in enumerate(pd.date_range(f'{2010+episode}-02-01', periods=12, freq='7D')):
                rows.append({'Episode': episode, 'DecisionDate': date, 'ExecutionDate': date+pd.Timedelta(days=1),
                             'EndDate': date+pd.Timedelta(days=30),
                             'EpisodeEndDecision': pd.Timestamp(f'{2010+episode}-06-01'),
                             'Horizon': 21, 'DeltaToBase': .01*np.sin(episode+i),
                             FEATURES[0]: .01*i, FEATURES[1]: .02*np.sin(i), FEATURES[2]: .1*episode})
        return pd.DataFrame(rows)

    def test_fit_uses_only_prior_closed_episodes_and_matured_outcomes(self):
        predictions = walk_forward(self.dataset())
        self.assertEqual(set(predictions.Episode), {5, 6})
        self.assertTrue((predictions.LastTrainingOutcome < predictions.DecisionDate).all())
        self.assertTrue((predictions.LastTrainingEpisodeEnd < predictions.DecisionDate).all())
        self.assertTrue((predictions.TrainingEpisodes == predictions.Episode-1).all())

    def test_current_or_future_episode_outcomes_cannot_change_earlier_predictions(self):
        original = self.dataset()
        modified = original.copy()
        modified.loc[modified.Episode >= 5, 'DeltaToBase'] += 100
        before = walk_forward(original)
        after = walk_forward(modified)
        np.testing.assert_array_equal(before.loc[before.Episode == 5, 'Prediction'],
                                      after.loc[after.Episode == 5, 'Prediction'])

    def test_duplicating_one_episode_does_not_increase_its_training_weight(self):
        data = self.dataset()
        train = data.loc[data.Episode <= 4]
        current = data.loc[data.Episode == 5].iloc[0]
        duplicated = pd.concat([train, train.loc[train.Episode == 1]])
        np.testing.assert_allclose(weighted_ridge(train, current, 10),
                                   weighted_ridge(duplicated, current, 10), atol=1e-12)


if __name__ == '__main__':
    unittest.main()
