#!/usr/bin/env python3
"""Regression tests for crop_cards trimming behavior."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent / 'Current-version'))

from crop_cards import trim_white_background


class TestCropCards(unittest.TestCase):
    def test_trim_keeps_near_white_card_border(self):
        # Simulate scanner paper with a card that has a very light border.
        image = np.full((300, 200, 3), 255, dtype=np.uint8)

        # Outer card border is near-white and should be preserved by the crop.
        image[30:271, 40:161] = 250

        # Card body is darker and easy to detect.
        image[40:261, 50:151] = 120

        cropped, rect = trim_white_background(image, white_threshold=244, padding=0)

        self.assertEqual(rect, (40, 30, 160, 270))
        self.assertEqual(cropped.shape[:2], (241, 121))

    def test_trim_returns_full_image_when_all_white(self):
        image = np.full((120, 80, 3), 255, dtype=np.uint8)

        cropped, rect = trim_white_background(image, white_threshold=244, padding=0)

        self.assertEqual(rect, (0, 0, 79, 119))
        self.assertEqual(cropped.shape[:2], (120, 80))

    def test_trim_keeps_main_region_when_touching_image_border(self):
        image = np.full((300, 200, 3), 255, dtype=np.uint8)

        # Simulate a card crop that touches top and bottom image bounds.
        image[:, 20:181] = 246

        # Add a darker center, plus a tiny interior blob that should not be selected.
        image[30:271, 30:171] = 120
        image[120:130, 185:195] = 100

        cropped, rect = trim_white_background(image, white_threshold=244, padding=0)

        self.assertEqual(rect, (20, 0, 180, 299))
        self.assertEqual(cropped.shape[:2], (300, 161))


if __name__ == '__main__':
    unittest.main()
