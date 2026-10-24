from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.models import JobOptions
from app.pipeline.ocr import _predict


class OcrPredictTests(unittest.TestCase):
    def test_quality_predict_does_not_enable_doc_preprocessor(self):
        pipeline = MagicMock()
        pipeline.predict.return_value = [{"text": "x"}]
        options = JobOptions(ocr_mode="quality", language="deu")
        _predict(pipeline, "/tmp/page.png", options)
        pipeline.predict.assert_called_once_with(
            "/tmp/page.png",
            use_doc_unwarping=False,
            use_doc_orientation_classify=False,
            use_queues=False,
        )


if __name__ == "__main__":
    unittest.main()
