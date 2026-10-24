from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.pipeline import ocr as ocr_module


class GpuMemoryTests(unittest.TestCase):
    def test_release_gpu_after_ocr_batch(self):
        ocr_module._pipeline_cache["fast:deu"] = MagicMock()
        self.assertFalse(ocr_module.release_gpu_after_ocr_batch(49, 50))
        self.assertTrue(ocr_module.release_gpu_after_ocr_batch(50, 50))
        self.assertEqual(ocr_module._pipeline_cache, {})

    def test_evict_pipeline_does_not_clear_cuda_cache(self):
        ocr_module._pipeline_cache.clear()
        mock = MagicMock()
        ocr_module._pipeline_cache["quality:deu"] = mock
        with unittest.mock.patch.object(ocr_module, "clear_ocr_cache") as clear_mock:
            from app.models import JobOptions

            ocr_module._evict_pipeline_for_options(JobOptions(ocr_mode="quality", language="deu"))
        clear_mock.assert_not_called()
        mock.close.assert_called_once()
        self.assertEqual(ocr_module._pipeline_cache, {})


if __name__ == "__main__":
    unittest.main()
