from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs import _cancelled, delete_job, list_library_jobs
from app.models import BookMetadata, JobOptions, JobProgress, JobState


class JobsLibraryTests(unittest.TestCase):
    def test_list_and_delete_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_id = "abc123456789"
            root = Path(tmp) / job_id
            root.mkdir()
            job = JobState(
                id=job_id,
                sources=["/tmp/book.pdf"],
                options=JobOptions(),
                status="done",
                metadata=BookMetadata(title="Testbuch"),
                progress=JobProgress(phase="done", total_pages=10, ocr_completed=10),
            )
            (root / "job.json").write_text(job.model_dump_json(indent=2), encoding="utf-8")
            with patch("app.jobs.jobs_root", return_value=Path(tmp)), patch(
                "app.jobs.job_dir", return_value=root
            ):
                items = list_library_jobs()
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["title"], "Testbuch")
                self.assertEqual(items[0]["status"], "done")

                delete_job(job_id)
                self.assertFalse(root.exists())
                self.assertEqual(list_library_jobs(), [])
                _cancelled.discard(job_id)


if __name__ == "__main__":
    unittest.main()
