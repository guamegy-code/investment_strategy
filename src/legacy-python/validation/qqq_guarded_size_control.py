"""Post-result controller ablation: price timing versus extra QQQ exposure."""
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qqq_guarded_reentry as core
from config import PROJECT_ROOT
from pension_structure_review import prepare
from result_paths import research_result_path

DESIGN = PROJECT_ROOT/'validation/qqq_guarded_size_control_20261027.json'


class StaticSizePolicy(core.ReentryPolicy):
    def amend(self, source, signal, market, portfolio, *, permit_entry=False):
        blocked, gate, _ = core.policy_inputs(source, signal, market)
        previous = self.active
        self.active = not blocked
        target = dict(signal['target'])
        if self.active:
            target['QQQ'] = min(1., target['QQQ']+((1-target['QQQ'])*.5 if self.profile == 'RULE_HALF' else .1))
            target['BIL'] = 1-target['QQQ']
        changed = self.previous_target is not None and any(abs(target[s]-self.previous_target[s]) > 1e-10 for s in target)
        transition = self.active != previous
        if self.active:
            weights = portfolio.weights({s: market[s]['Close'] for s in ('QQQ', 'BIL')})
            deviation = max(abs(weights.get(s, 0.)-target[s]) for s in target)
            rebalance = transition or changed or deviation >= .075 or weights.get('QQQ', 0.)-target['QQQ'] <= -.04
        else:
            rebalance = bool(signal['rebalance']) or transition
        if blocked and target != signal['target']:
            raise AssertionError('Static control changed a protected target')
        self.previous_target = dict(target)
        return {**signal, 'target': target, 'rebalance': rebalance,
                'reason': 'STATIC_SIZE_CONTROL' if self.active or transition else signal.get('reason')}, blocked, gate


def main():
    rows = []
    with TemporaryDirectory(prefix='qqq-size-control-', dir=PROJECT_ROOT/'tmp') as temp:
        directory = prepare(Path(temp), proxy=False)
        data = core.load_case(directory, 'CURRENT')
        # Controller substitution is isolated to this process and context;
        # all original source calculations, orders and costs use the same runner.
        with patch.object(core, 'ReentryPolicy', StaticSizePolicy):
            for scenario, settings in core.LOCK['scenarios'].items():
                for profile in ('RULE10', 'RULE_HALF'):
                    h, trades, _ = core.simulate(data, 30, 'CURRENT', profile, **settings)
                    stamp = f'qqq_guarded_static_{scenario.lower()}_{profile.lower()}'
                    h.to_csv(research_result_path(stamp+'_daily.csv'))
                    trades.to_csv(research_result_path(stamp+'_trades.csv'), index=False)
                    rows.append({'Scenario': scenario, 'Profile': profile, **core.summary(h, trades)})
                    pd.DataFrame(rows).to_csv(research_result_path('qqq_guarded_static_summary.csv'), index=False)
                    print(rows[-1], flush=True)
        prepared = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.csv')}
    paths = [DESIGN, Path(__file__), Path(core.__file__), core.LOCK_PATH]
    record = {'Design': json.loads(DESIGN.read_text(encoding='utf-8')), 'PreparedSHA256': prepared,
              'InputSHA256': {str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    research_result_path('qqq_guarded_static_manifest.json').write_text(json.dumps(record, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
