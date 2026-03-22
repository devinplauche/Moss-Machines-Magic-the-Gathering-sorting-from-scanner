#!/usr/bin/env python3
import sys
import unittest

sys.path.insert(0, r'Current-version')

from card_filter import CardFilter


class TestCardFilter(unittest.TestCase):
    def setUp(self):
        self.cards = [
            {
                'product_id': '1',
                'name': 'Lightning Bolt',
                'color': 'R',
                'cmc': 1,
                'set_code': 'M10',
                'number': '146',
                'type': 'Instant',
                'subTypeName': 'Normal',
            },
            {
                'product_id': '2',
                'name': 'Counterspell',
                'color': 'U',
                'cmc': 2,
                'set_code': '7ED',
                'number': '67',
                'type': 'Instant',
                'subTypeName': 'Normal',
            },
            {
                'product_id': '3',
                'name': 'Sol Ring',
                'color': 'Colorless',
                'cmc': 1,
                'set_code': 'CMM',
                'number': '409',
                'type': 'Artifact',
                'subTypeName': 'Normal',
            },
            {
                'product_id': '4',
                'name': 'Assassin\'s Trophy',
                'color': 'B,G',
                'cmc': 2,
                'set_code': 'GRN',
                'number': '152',
                'type': 'Instant',
                'subTypeName': 'Normal',
            },
        ]
        self.filter = CardFilter()

    def test_name_exact(self):
        out, diag, scores = self.filter.apply(self.cards, {'name': 'Lightning Bolt'})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['product_id'], '1')
        self.assertIn('name', diag['applied_filters'])
        self.assertGreaterEqual(scores['1'], 1)

    def test_name_fuzzy_fallback(self):
        out, diag, _ = self.filter.apply(self.cards, {'name': 'Lightnng Bolt'})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['product_id'], '1')
        self.assertIn('name', diag['applied_filters'])

    def test_color_identity_multi(self):
        out, _, _ = self.filter.apply(self.cards, {'color_identity': 'multi'})
        self.assertEqual([c['product_id'] for c in out], ['4'])

    def test_color_identity_colorless(self):
        out, _, _ = self.filter.apply(self.cards, {'color_identity': 'colorless'})
        self.assertEqual([c['product_id'] for c in out], ['3'])

    def test_cmc_within_one(self):
        out, diag, _ = self.filter.apply(self.cards, {'cmc': 2})
        # cards with cmc 1 and 2 are retained by +/-1 rule
        self.assertEqual({c['product_id'] for c in out}, {'1', '2', '3', '4'})
        self.assertIn('cmc', diag['applied_filters'])

    def test_bonus_filters_set_and_collector_number(self):
        out, diag, _ = self.filter.apply(self.cards, {'set_code': 'M10', 'collector_number': '146'})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['product_id'], '1')
        self.assertIn('set', diag['applied_filters'])
        self.assertIn('collector_number', diag['applied_filters'])

    def test_disable_filter_toggle(self):
        cf = CardFilter({'color_identity': False})
        out, diag, _ = cf.apply(self.cards, {'color_identity': 'U'})
        self.assertEqual(len(out), len(self.cards))
        self.assertNotIn('color_identity', diag['applied_filters'])

    def test_no_match_returns_empty_for_caller_fallback(self):
        out, diag, _ = self.filter.apply(self.cards, {'name': 'Definitely Not A Card'})
        self.assertEqual(len(out), 0)
        self.assertGreaterEqual(diag['filters_used'], 1)


if __name__ == '__main__':
    unittest.main()
