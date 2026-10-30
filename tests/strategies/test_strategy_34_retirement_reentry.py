"""Protect the original 31 and the separately published reentry strategy."""
import hashlib
import json
import unittest

import yaml

from tests import PROJECT_ROOT
from strategy_dsl import load_strategy_directory


def definition_hash(filename, *, rules_only=False):
    definition = yaml.load((PROJECT_ROOT/'strategies'/filename).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    if rules_only:
        definition.pop('strategy')
    return hashlib.sha256(json.dumps(definition, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Strategy34Tests(unittest.TestCase):
    def test_original_31_and_31p_definitions_match_pre_reentry_revision(self):
        # Canonical YAML hashes from d5b3ac1; comments and line endings are ignored.
        expected = {
            '31_band_7030_tdf_valuation_credit_guard_no_topup.yaml': '2d3b1abe13703249ee25ef7e301e85c2ef6a2c2f5f84d857d135e47a447392dd',
            '31P_band_7030_tdf_valuation_credit_guard_no_topup_kodex_koact.yaml': '0ad033c3b2d70940215e83c9701c8488f6082748f5103d4174f8c45eb89462bb',
        }
        for filename, expected_hash in expected.items():
            self.assertEqual(definition_hash(filename), expected_hash, filename)

    def test_34_preserves_every_rule_of_the_validated_reentry_candidate(self):
        # e6c1e7d's validated reentry rules, now given an independent identity.
        self.assertEqual(definition_hash('34_band_7030_tdf_credit_guard_reentry.yaml', rules_only=True),
                         '6e5a90c7356c874bbc812f605caf1aba93637051b8b43c233124201dbd416907')

    def test_both_product_mappings_resolve_to_their_own_source(self):
        strategies = {s.strategy_id: s for s in load_strategy_directory(PROJECT_ROOT/'strategies')}
        original = strategies['dsl:band-7030-tdf-valuation-credit-guard-no-topup-kodex-koact']
        improved = strategies['dsl:band-7030-tdf-credit-guard-reentry-kodex-koact']
        self.assertEqual(original.source_strategy_id, 'band-7030-tdf-valuation-credit-guard-no-topup')
        self.assertEqual(improved.source_strategy_id, 'band-7030-tdf-credit-guard-reentry')
        self.assertEqual(original.products, improved.products)
        self.assertNotIn('guarded_reentry', original.source_strategy.definition['state'])
        self.assertIn('guarded_reentry', improved.source_strategy.definition['state'])
        self.assertEqual(improved.definition['strategy']['version'], 1)


if __name__ == '__main__': unittest.main()
