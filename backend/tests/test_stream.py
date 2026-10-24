import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models import PageResult, SourcePlan, SourcePlanEntry
from app.pipeline.checkpoint import (
    batch_sources_ocr_complete,
    batch_sources_preprocessed,
    can_resume,
    count_completed_ocr,
    load_source_plan,
    merge_preprocess_checkpoint,
    pages_for_source_batch,
    save_run_checkpoint,
    save_source_plan,
)
from app.pipeline.source_plan import build_source_plan, should_stream


class BlockQueueTests(unittest.TestCase):
    def test_next_block_is_prepared_while_current_is_consumed(self):
        import threading
        import time

        from app.pipeline.stream import run_block_queue

        events: list[str] = []
        lock = threading.Lock()

        def prepare(block: int) -> int:
            with lock:
                events.append(f"prep-start-{block}")
            time.sleep(0.05)
            with lock:
                events.append(f"prep-end-{block}")
            return block

        def consume(block: int) -> None:
            with lock:
                events.append(f"ocr-start-{block}")
            time.sleep(0.12)
            with lock:
                events.append(f"ocr-end-{block}")

        run_block_queue([1, 2], prepare, consume)
        self.assertLess(events.index("prep-start-2"), events.index("ocr-end-1"))


class StreamCheckpointTests(unittest.TestCase):
    def test_should_stream_for_multi_page_plan(self):
        plan = SourcePlan(
            entries=[
                SourcePlanEntry(source_index=0, source_path="/tmp/a.pdf", pdf_page=0),
                SourcePlanEntry(source_index=1, source_path="/tmp/a.pdf", pdf_page=1),
            ]
        )
        self.assertTrue(should_stream(plan, test_page_only=False, checkpoint_pages=True))
        self.assertFalse(should_stream(plan, test_page_only=True, checkpoint_pages=True))

    def test_batch_source_tracking(self):
        pages = [
            PageResult(index=0, source_path="a.pdf", image_path="p0.png", source_index=0),
            PageResult(index=1, source_path="a.pdf", image_path="p1.png", source_index=1),
        ]
        batch = {0, 1}
        self.assertTrue(batch_sources_preprocessed(pages, batch))
        self.assertFalse(batch_sources_ocr_complete(Path("/missing"), pages, batch))

    def test_pages_for_source_batch(self):
        pages = [
            PageResult(index=0, source_path="a.pdf", image_path="p0.png", source_index=10),
            PageResult(index=1, source_path="a.pdf", image_path="p1.png", source_index=11),
            PageResult(index=2, source_path="a.pdf", image_path="p2.png", source_index=20),
        ]
        selected = pages_for_source_batch(pages, {10, 11})
        self.assertEqual([page.index for page in selected], [0, 1])

    def test_resume_requires_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = SourcePlan(
                entries=[SourcePlanEntry(source_index=0, source_path="/tmp/a.pdf", pdf_page=0)]
            )
            save_source_plan(root, plan)
            save_run_checkpoint(
                root,
                phase="ocr",
                total_pages=0,
                ocr_completed=0,
                batch_size=1,
                next_source_index=0,
                source_count=1,
                streaming=True,
            )
            self.assertFalse(can_resume(root))

            merge_preprocess_checkpoint(
                root,
                [PageResult(index=0, source_path="a.pdf", image_path="p0.png", source_index=0)],
            )
            (root / "ocr").mkdir(parents=True)
            (root / "ocr" / "0000.json").write_text(
                PageResult(
                    index=0,
                    source_path="a.pdf",
                    image_path="p0.png",
                    source_index=0,
                    markdown="Hallo",
                ).model_dump_json(),
                encoding="utf-8",
            )
            save_run_checkpoint(
                root,
                phase="ocr",
                total_pages=1,
                ocr_completed=0,
                batch_size=1,
                next_source_index=0,
                source_count=2,
                streaming=True,
            )
            plan = SourcePlan(
                entries=[
                    SourcePlanEntry(source_index=0, source_path="/tmp/a.pdf", pdf_page=0),
                    SourcePlanEntry(source_index=1, source_path="/tmp/a.pdf", pdf_page=1),
                ]
            )
            save_source_plan(root, plan)
            self.assertTrue(can_resume(root))

    @patch("app.pipeline.source_plan.pdf_page_count", return_value=3)
    @patch("app.pipeline.source_plan.collect_input_files")
    def test_build_source_plan_for_pdf(self, collect_files, _count):
        collect_files.return_value = [Path("/tmp/book.pdf")]
        plan = build_source_plan(["/tmp/book.pdf"])
        self.assertEqual(plan.source_count, 3)
        self.assertEqual(plan.entries[2].pdf_page, 2)
        loaded = load_source_plan(Path("/tmp/nope"))
        self.assertIsNone(loaded)


if __name__ == "__main__":
    unittest.main()
