"""Supplemental stress tests of the predeclared directional combinations."""
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qqq_adaptive_speed as study
from result_paths import research_result_path

DESIGN = study.PROJECT_ROOT/'validation/qqq_adaptive_direction_stress_20261027.json'
LOCK = json.loads(DESIGN.read_text(encoding='utf-8'))


def main():
    rows = []
    with TemporaryDirectory(prefix='qqq-direction-', dir=study.PROJECT_ROOT/'tmp') as temp:
        directory = study.core.prepare(Path(temp), proxy=False)
        data = study.load_case(directory, 'CURRENT')
        for scenario in LOCK['scenarios']:
            for number in LOCK['sources']:
                for profile in LOCK['profiles']:
                    h, trades = study.simulate(data, number, 'CURRENT', profile, **study.LOCK['scenarios'][scenario])
                    name = study.stamp(scenario, 'CURRENT', number, profile)
                    h.to_csv(research_result_path(name+'_daily.csv'))
                    trades.to_csv(research_result_path(name+'_trades.csv'), index=False)
                    row = {'Period': 'CURRENT', 'Source': number, 'Scenario': scenario,
                           'Profile': profile, **study.summary(h, trades)}
                    rows.append(row)
                    print(row, flush=True)
        prepared = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.csv')}
    pd.DataFrame(rows).to_csv(research_result_path('qqq_adaptive_direction_stress_summary.csv'), index=False)
    manifest = {'Design': LOCK, 'PrimaryDesign': study.LOCK, 'PreparedSHA256': prepared,
                'InputSHA256': {str(p.relative_to(study.PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in [*study.input_paths(), DESIGN, Path(__file__)]}}
    research_result_path('qqq_adaptive_direction_stress_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
