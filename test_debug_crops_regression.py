#!/usr/bin/env python3
"""Regression tests for debug_crops image detection behavior."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'Current-version'))

from optimized_scanner import OptimizedCardScanner


class TestDebugCropsRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).parent
        cls.debug_dir = cls.repo_root / 'Current-version' / 'debug_crops'
        cls.scanner = OptimizedCardScanner(
            db_path=str(cls.repo_root / 'unified_card_database.db'),
            max_workers=4,
            cache_enabled=False,
            enable_collection=False,
        )

    @classmethod
    def tearDownClass(cls):
        cls.scanner.close()

    def test_missing_file_raises(self):
        missing = self.debug_dir / 'this-file-does-not-exist.jpg'
        with self.assertRaises(FileNotFoundError):
            self.scanner.scan_from_file(str(missing), threshold=40, top_n=1)

    def test_debug_crops_filename_cards_match(self):
        expected_by_file = {
            'hisokas-defiance.jpg': 'hisokas defiance',
            'ons-120-voidmage-prodigy.jpg': 'voidmage prodigy',
        }

        for filename, expected in expected_by_file.items():
            with self.subTest(filename=filename):
                matches, _elapsed = self.scanner.scan_from_file(
                    str(self.debug_dir / filename),
                    threshold=40,
                    top_n=1,
                )
                self.assertTrue(matches, f'No matches returned for {filename}')
                top = matches[0]
                actual_name = str(top.get('name') or '').lower().replace("'", '')
                self.assertIn(expected.replace("'", ''), actual_name)
                self.assertIn('magic', str(top.get('game') or '').lower())

    def test_img_3490_uses_cropped_path_and_magic_result(self):
        target = self.debug_dir / 'IMG_3490.jpg'
        matches, _elapsed = self.scanner.scan_from_file(str(target), threshold=40, top_n=1)

        self.assertTrue(matches, 'IMG_3490.jpg should produce at least one candidate')
        top = matches[0]
        self.assertIn('magic', str(top.get('game') or '').lower())
        self.assertIn(top.get('scan_source'), {'manual_crop', 'contour_crop'})


if __name__ == '__main__':
    unittest.main()
