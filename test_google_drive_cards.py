#!/usr/bin/env python3
"""
Regression tests for Google Drive scanner images.

Tests correctness of card identification for known problematic samples:
 - white/light-background cards (dark text on light frame)
 - mid-brightness / gray-frame cards (where Stage-1 previously over-filtered)
 - cards that previously produced wrong matches (Bitterbloom Bearer false positives)
 - positive control: black-frame card that was always identified correctly

Images are loaded from Current-version/google_drive_downloads/.
Tests are skipped gracefully when an image is not present locally.
"""

import sys
import os
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT / 'Current-version'))

import cv2
from optimized_scanner import OptimizedCardScanner


def _resolve_image(name: str) -> Path | None:
    """Return path to a downloaded Drive image, or None if not accessible."""
    import glob
    dl_dir = REPO_ROOT / 'Current-version' / 'google_drive_downloads'
    direct = dl_dir / name
    if direct.exists():
        return direct
    matches = sorted(dl_dir.glob(f'*_{name}'))
    return matches[0] if matches else None


class TestGoogleDriveKnownCards(unittest.TestCase):
    """End-to-end recognition tests on Google Drive sample images."""

    CONFIDENCE_PASS = 55.0   # minimum acceptable confidence for a name match
    BAD_MATCH_NAME  = 'bitterbloom bearer'  # common false-positive we want to eradicate

    @classmethod
    def setUpClass(cls):
        cls.scanner = OptimizedCardScanner(
            db_path=str(REPO_ROOT / 'Current-version' / 'unified_card_database.db'),
            max_workers=4,
            cache_enabled=True,
            enable_collection=False,
        )

    @classmethod
    def tearDownClass(cls):
        cls.scanner.close()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _scan(self, filename: str):
        path = _resolve_image(filename)
        if path is None:
            self.skipTest(f'Image not available locally: {filename}')
        matches, _elapsed = self.scanner.scan_from_file(str(path), threshold=40, top_n=5)
        return matches, path

    def _assert_correct_name(self, matches, expected_fragment: str, filename: str):
        self.assertTrue(matches, f'{filename}: no matches returned')
        top_name = str(matches[0].get('name') or '').lower()
        conf     = float(matches[0].get('confidence') or 0)
        self.assertIn(
            expected_fragment.lower(), top_name,
            f'{filename}: expected name to contain "{expected_fragment}" but got "{top_name}" (conf={conf:.1f})',
        )
        self.assertGreater(
            conf, self.CONFIDENCE_PASS,
            f'{filename}: confidence {conf:.1f} < threshold {self.CONFIDENCE_PASS}',
        )

    def _assert_not_false_positive(self, matches, bad_name_fragment: str, filename: str):
        """Assert the top match is NOT the known false-positive card."""
        if not matches:
            return  # No match at all is acceptable here
        top_name = str(matches[0].get('name') or '').lower()
        self.assertNotIn(
            bad_name_fragment.lower(), top_name,
            f'{filename}: matched known false-positive "{bad_name_fragment}"; expected a different card.',
        )

    # ── positive control ─────────────────────────────────────────────────────

    def test_18030001_black_frame_positive_control(self):
        """Color18030001.jpg has a black frame and was always identified correctly.
        Ensure it still passes after refactoring."""
        matches, path = self._scan('Color18030001.jpg')
        self.assertTrue(bool(matches), f'{path.name}: should return at least one match')
        game = str(matches[0].get('game') or '').lower()
        self.assertIn('magic', game, f'{path.name}: expected Magic card, got game={game!r}')

    # ── previously failing: Stage-1 over-filter produced wrong card ──────────

    def test_18060001_not_bitterbloom_bearer(self):
        """Color18060001.jpg was incorrectly matched as 'Bitterbloom Bearer'.
        After disabling the full-DB stage-1 fallback this must no longer happen."""
        matches, path = self._scan('Color18060001.jpg')
        self._assert_not_false_positive(matches, self.BAD_MATCH_NAME, path.name)

    def test_18050001_holy_armor(self):
        """Color18050001.jpg is Holy Armor but was matched to a box product.
        Ensure the result is a real card (not a box/display product) or no match."""
        matches, path = self._scan('Color18050001.jpg')
        if not matches:
            return  # No match is acceptable after the over-filter fix
        top_name = str(matches[0].get('name') or '')
        # Box products contain words like 'display', 'deck box', 'theme deck'
        for forbidden in ('display', 'theme deck', 'deck box', 'booster box'):
            self.assertNotIn(
                forbidden.lower(), top_name.lower(),
                f'{path.name}: matched box product "{top_name}" instead of a card',
            )

    # ── white-on-gray OCR samples ─────────────────────────────────────────────

    def test_18130001_power_sink_ocr(self):
        """Color18130001.jpg had title 'Dower Sink' read by OCR (should resolve to Power Sink)."""
        matches, path = self._scan('Color18130001.jpg')
        self._assert_correct_name(matches, 'Power Sink', path.name)

    def test_18110002_resolves_without_false_positive(self):
        """Color18110002.jpg had blank OCR and was previously matched wrongly.
        After the stage-1 fix it should either return a correct match or no match."""
        matches, path = self._scan('Color18110002.jpg')
        self._assert_not_false_positive(matches, self.BAD_MATCH_NAME, path.name)

    # ── known-good samples: should still work ────────────────────────────────

    def test_13640001_centaur_peacemaker(self):
        """Color13640001.jpg should still identify correctly as Centaur Peacemaker."""
        matches, path = self._scan('Color13640001.jpg')
        self._assert_correct_name(matches, 'Centaur Peacemaker', path.name)

    def test_13990001_siege_wurm(self):
        """Color13990001.jpg should still identify correctly as Siege Wurm."""
        matches, path = self._scan('Color13990001.jpg')
        self._assert_correct_name(matches, 'Siege Wurm', path.name)

    # ── OCR gibberish correction ──────────────────────────────────────────────

    def test_ocr_gibberish_dower_resolves_to_power(self):
        """Unit-level check: resolver must fix 'Dower Sink' -> 'Power Sink'."""
        result = self.scanner._resolve_ocr_name_candidate('Dower Sink')
        self.assertIsNotNone(result, "Expected 'Power Sink', got None")
        self.assertIn('power sink', str(result).lower())

    def test_ocr_gibberish_dee_sey_resolves_to_seeker(self):
        """Unit-level check: resolver must fix 'Dee sey' -> 'Seeker'."""
        result = self.scanner._resolve_ocr_name_candidate('Dee sey')
        self.assertIsNotNone(result, "Expected 'Seeker', got None")
        self.assertIn('seeker', str(result).lower())

    # ── crop tool smoke test ──────────────────────────────────────────────────

    def test_16410001_crops_and_trims_correctly(self):
        """Color16410001.jpg (reference image) should trim down noticeably from full size."""
        import numpy as np
        path = _resolve_image('Color16410001.jpg')
        if path is None:
            self.skipTest('Color16410001.jpg not available')

        img = cv2.imread(str(path))
        self.assertIsNotNone(img)
        orig_h, orig_w = img.shape[:2]

        trimmed = self.scanner._trim_white_background(img, white_threshold=244, min_shrink_ratio=0.02, padding=6)
        new_h, new_w = trimmed.shape[:2]

        # Crop should be strictly smaller (we know margins exist on all sides)
        self.assertLess(new_w, orig_w, 'Width should decrease after trimming')
        self.assertLess(new_h, orig_h, 'Height should decrease after trimming')

        # Aspect ratio should be close to standard MTG card (0.714 ± 0.09)
        aspect = new_w / new_h
        self.assertAlmostEqual(aspect, 0.714, delta=0.09,
            msg=f'Trimmed aspect ratio {aspect:.3f} is far from MTG standard 0.714')


if __name__ == '__main__':
    unittest.main(verbosity=2)
