from __future__ import annotations

import unittest

import cv2
import numpy as np

from app.pipeline.preprocess import _flatten_baselines, _line_baselines, _whiten_paper


def _bowed_page() -> np.ndarray:
    image = np.full((420, 320, 3), 210, np.uint8)
    for base in range(36, 390, 26):
        for x in range(24, 296):
            y = int(base + 16 * np.sin(np.pi * (x - 24) / 272))
            y = min(max(y, 1), 414)
            image[y : y + 3, x] = 15
    return image


class DewarpTests(unittest.TestCase):
    def test_whiten_lifts_shaded_paper(self):
        page = np.zeros((180, 140, 3), np.uint8)
        for y in range(180):
            page[y, :] = int(120 + y * 0.4)
        page[40:48, 20:120] = 20
        whitened = _whiten_paper(page)
        paper = whitened[10:30, 20:120]
        self.assertGreater(float(paper.mean()), 230)

    def test_flatten_reduces_baseline_bow(self):
        page = _bowed_page()
        gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
        before = _line_baselines(gray)
        self.assertGreaterEqual(len(before), 4)
        bow = max(float(np.std(line)) for line in before)
        flat = _flatten_baselines(page)
        after = _line_baselines(cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY))
        self.assertGreaterEqual(len(after), 4)
        remain = max(float(np.std(line)) for line in after)
        self.assertLess(remain, bow * 0.55)
