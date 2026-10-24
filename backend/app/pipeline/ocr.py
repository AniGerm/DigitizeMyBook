from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image

from ..models import Block, JobOptions, PageResult
from .page_numbers import is_margin_page_hint, parse_printed_page_number, pick_printed_page
from .structure import is_markdown_heading, is_section_title
from .text_cleanup import cleanup_german_ocr

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
logger = logging.getLogger(__name__)


DROP_LABELS = {
    "header",
    "footer",
    "header_image",
    "footer_image",
    "number",
    "aside_text",
    "footnote",
    "vision_footnote",
}

TITLE_LABELS = {"doc_title", "paragraph_title", "content_title", "title"}

_pipeline_cache: dict[str, Any] = {}


def _close_pipeline(pipeline: Any) -> None:
    close = getattr(pipeline, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        logger.debug("Pipeline konnte nicht geschlossen werden.", exc_info=True)


def _evict_pipeline_for_options(options: JobOptions) -> None:
    """Drop one cached pipeline without CUDA cache flush (safe during an OCR block)."""
    key = f"{options.ocr_mode}:{options.language}"
    pipeline = _pipeline_cache.pop(key, None)
    if pipeline is not None:
        _close_pipeline(pipeline)


def clear_ocr_cache(*, reason: str = "block_end") -> None:
    """Release OCR pipelines and return GPU memory (only between OCR blocks)."""
    import gc

    logger.info("GPU-OCR-Cache freigegeben (%s)", reason)
    for pipeline in _pipeline_cache.values():
        _close_pipeline(pipeline)
    _pipeline_cache.clear()
    gc.collect()
    try:
        import paddle

        if paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
            paddle.device.cuda.empty_cache()
    except Exception:
        pass


def release_gpu_after_ocr_batch(completed: int, batch_size: int) -> bool:
    """Free GPU memory every `batch_size` finished pages (e.g. 50)."""
    if batch_size <= 0 or completed <= 0 or completed % batch_size != 0:
        return False
    clear_ocr_cache()
    return True


def gpu_status() -> dict[str, Any]:
    info: dict[str, Any] = {
        "paddle_installed": False,
        "gpu_available": False,
        "device_name": None,
        "cuda_version": None,
        "error": None,
    }
    try:
        import paddle

        info["paddle_installed"] = True
        info["gpu_available"] = bool(paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0)
        if info["gpu_available"]:
            try:
                info["device_name"] = paddle.device.cuda.get_device_name(0)
            except Exception:
                info["device_name"] = "CUDA GPU"
        info["cuda_version"] = getattr(paddle.version, "cuda", lambda: None)()
    except Exception as exc:
        info["error"] = str(exc)
    return info


def _lang_for(options: JobOptions) -> str | None:
    if options.language == "deu":
        return "german"
    if options.language == "eng":
        return "en"
    return None


def _ensure_dynamic() -> None:
    try:
        import paddle

        paddle.disable_static()
    except Exception:
        pass


def _make_structure_pipeline(lang: str | None):
    from paddleocr import PPStructureV3

    return PPStructureV3(
        device="gpu",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        markdown_ignore_labels=["number", "footnote", "header", "header_image", "footer", "footer_image", "aside_text"],
        lang=lang,
        use_seal_recognition=False,
        use_chart_recognition=False,
        use_formula_recognition=False,
        use_table_recognition=False,
    )


def _make_book_ocr_pipeline(lang: str | None):
    from paddleocr import PaddleOCR

    return PaddleOCR(
        device="gpu",
        lang=lang or "german",
        ocr_version="PP-OCRv5",
        use_doc_orientation_classify=True,
        use_doc_unwarping=True,
        use_textline_orientation=True,
        text_det_limit_side_len=2400,
        text_recognition_model_name="latin_PP-OCRv5_mobile_rec",
        text_rec_score_thresh=0.45,
    )


def _get_pipeline(options: JobOptions):
    _ensure_dynamic()
    key = f"{options.ocr_mode}:{options.language}"
    if key in _pipeline_cache:
        return _pipeline_cache[key]
    for stale_key in list(_pipeline_cache):
        if stale_key != key:
            _close_pipeline(_pipeline_cache.pop(stale_key))
    lang = _lang_for(options)
    try:
        if options.ocr_mode == "quality":
            from paddleocr import PaddleOCRVL

            pipeline = PaddleOCRVL(
                device="gpu",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_queues=False,
                vl_rec_backend="native",
                markdown_ignore_labels=["number", "footnote", "header", "header_image", "footer", "footer_image", "aside_text"],
            )
        else:
            pipeline = _make_book_ocr_pipeline(lang)
    except Exception as exc:
        if options.ocr_mode == "quality":
            logger.exception("Qualitäts-OCR (PaddleOCR-VL) konnte nicht geladen werden")
            raise RuntimeError(
                "Qualitäts-OCR konnte nicht gestartet werden. "
                "GPU-Speicher prüfen oder Schnell-Modus wählen."
            ) from exc
        logger.exception("OCR-Pipeline %s konnte nicht geladen werden", options.ocr_mode)
        from paddleocr import PaddleOCR

        pipeline = PaddleOCR(device="gpu", lang=lang or "german")
    _pipeline_cache[key] = pipeline
    return pipeline


def _as_dict(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    if hasattr(result, "get") and hasattr(result, "keys"):
        try:
            mapping = {k: result[k] for k in list(result.keys())}
            if mapping:
                return mapping
        except Exception:
            pass
    for attr in ("json", "res"):
        value = getattr(result, attr, None)
        if callable(value):
            try:
                value = value()
            except TypeError:
                pass
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
    if hasattr(result, "items"):
        try:
            return dict(result)
        except Exception:
            pass
    return {}


def _pick(mapping: Any, *keys: str, default: Any = None) -> Any:
    if mapping is None:
        return default
    getter = mapping.get if hasattr(mapping, "get") else None
    for key in keys:
        try:
            value = getter(key) if getter else mapping[key]
        except Exception:
            continue
        if value is None:
            continue
        size = getattr(value, "size", None)
        if size == 0:
            continue
        if isinstance(value, (list, tuple, dict, str)) and len(value) == 0:
            continue
        return value
    return default


def _as_sequence(value: Any) -> list:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _bbox(value: Any) -> list[float]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, dict):
        return _bbox(_pick(value, "bbox", "coordinate", "points", default=[]))
    if isinstance(value, (list, tuple)):
        flat: list[float] = []
        for item in value:
            if isinstance(item, (list, tuple)):
                flat.extend(float(x) for x in item[:2])
            else:
                try:
                    flat.append(float(item))
                except (TypeError, ValueError):
                    continue
        if len(flat) >= 4:
            xs = flat[0::2]
            ys = flat[1::2]
            if len(xs) > 2:
                return [min(xs), min(ys), max(xs), max(ys)]
            return [flat[0], flat[1], flat[2], flat[3]]
    return []


def _ocr_payload(data: dict[str, Any]) -> dict[str, Any]:
    nested = _pick(data, "overall_ocr_res", default={})
    if isinstance(nested, dict) and nested:
        return nested
    nested_dict = _as_dict(nested)
    return nested_dict if nested_dict else data


def _guess_title_line(text: str, bbox: list[float], page: PageResult) -> bool:
    return is_section_title(text, bbox, page)


def _line_entries(blocks: list[Block]) -> list[tuple[float, str]]:
    lines: list[tuple[float, str]] = []
    for block in blocks:
        text = block.content.strip()
        if not text or parse_printed_page_number(text) is not None:
            continue
        if len(block.bbox) < 4:
            continue
        lines.append((block.bbox[1], text))
    return lines


def _lines_read_bottom_up(blocks: list[Block]) -> bool:
    """Detect pages where OCR lines were sorted top-down but narrative runs bottom-up."""
    lines = _line_entries(blocks)
    if len(lines) < 4:
        return False
    lines.sort(key=lambda item: item[0])
    top, bottom = lines[0][1], lines[-1][1]
    if not top or not bottom:
        return False
    top_lower = top[0].islower()
    top_end = top.rstrip().endswith((".", "!", "?", "»", "«", ";", ":"))
    bottom_start = bottom[0].isupper() or bottom.startswith(("»", "„", "«"))
    if bottom_start and top_lower and top_end:
        return True
    # Strong signal: bottom line looks like a fresh paragraph, top like a trailing fragment.
    top_words = top.split()
    bottom_words = bottom.split()
    if (
        bottom_start
        and len(bottom_words) >= 4
        and top_lower
        and len(top_words) <= 8
        and not top.endswith("-")
    ):
        return True
    return False


def _rotate_page_image(page: PageResult) -> None:
    import cv2

    image = cv2.imread(page.image_path)
    if image is None:
        return
    rotated = cv2.rotate(image, cv2.ROTATE_180)
    cv2.imwrite(page.image_path, rotated)
    page.height, page.width = rotated.shape[:2]


def _fix_reading_order(page: PageResult) -> None:
    if not _lines_read_bottom_up(page.blocks):
        return
    page.blocks.reverse()
    _rotate_page_image(page)


def _fix_reading_order_merged(page: PageResult) -> None:
    """Correct upside-down pages after line merge when Y-sort scrambled reading order."""
    for block in page.blocks:
        if not block.content:
            continue
        lines = [line for line in block.content.split("\n") if line.strip()]
        if len(lines) < 4:
            continue
        probe = [
            Block(label="text", content=line, bbox=[0, index * 10, 100, index * 10 + 8], order=index)
            for index, line in enumerate(lines)
        ]
        if not _lines_read_bottom_up(probe):
            continue
        block.content = "\n".join(reversed(lines))
        _rotate_page_image(page)
        return


def _sort_blocks_for_reading(blocks: list[Block]) -> list[Block]:
    """Prefer OCR reading order; bbox Y alone breaks oriented pages."""
    if blocks and all(block.order is not None for block in blocks):
        return sorted(
            blocks,
            key=lambda block: (
                block.order if block.order is not None else 10**6,
                block.bbox[1] if len(block.bbox) >= 4 else 0,
            ),
        )
    return sorted(
        blocks,
        key=lambda block: (
            block.bbox[1] if len(block.bbox) >= 4 else 0,
            block.bbox[0] if len(block.bbox) >= 2 else 0,
            block.order if block.order is not None else 10**6,
        ),
    )


def _extract_line_blocks(data: dict[str, Any], page: PageResult) -> list[Block]:
    payload = _ocr_payload(data)
    rec_texts = _as_sequence(_pick(payload, "rec_texts", "rec_text", default=[]))
    rec_boxes = _as_sequence(_pick(payload, "rec_boxes", "rec_polys", default=[]))
    rec_scores = _as_sequence(_pick(payload, "rec_scores", default=[]))
    page_hints: list[tuple[str, list[float], PageResult]] = []
    blocks: list[Block] = []
    for index, raw_text in enumerate(rec_texts):
        text = cleanup_german_ocr(str(raw_text or ""))
        if not text:
            continue
        score = float(rec_scores[index]) if index < len(rec_scores) else 1.0
        if score < 0.45 and len(text) < 4:
            continue
        if len(text) == 1 and not text.isdigit():
            continue
        box = rec_boxes[index] if index < len(rec_boxes) else []
        bbox = _bbox(box)
        if parse_printed_page_number(text) is not None:
            page_hints.append((text, bbox, page))
            continue
        label = "paragraph_title" if _guess_title_line(text, bbox, page) else "text"
        blocks.append(
            Block(
                label=label,
                content=text,
                bbox=bbox,
                order=index,
            )
        )
    page.printed_page = pick_printed_page(page_hints)
    return blocks


def _extract_blocks(data: dict[str, Any], page: PageResult, image_dir: Path) -> list[Block]:
    line_blocks = _extract_line_blocks(data, page)
    if line_blocks:
        return line_blocks

    raw_blocks = _pick(data, "parsing_res_list", "layout_det_res", "blocks", default=[])
    if isinstance(raw_blocks, dict):
        raw_blocks = _pick(raw_blocks, "boxes", "layout_det_res", default=[])
    raw_blocks = _as_sequence(raw_blocks)
    blocks: list[Block] = []
    for index, item in enumerate(raw_blocks):
        if not isinstance(item, dict):
            bbox_val = getattr(item, "bbox", None)
            if bbox_val is None:
                bbox_val = getattr(item, "block_bbox", [])
            content_val = getattr(item, "content", None)
            if content_val is None:
                content_val = getattr(item, "block_content", "") or ""
            item = {
                "block_label": getattr(item, "label", None) or getattr(item, "block_label", "text"),
                "block_content": content_val,
                "block_bbox": bbox_val,
                "block_order": getattr(item, "order", None) or getattr(item, "block_order", index),
            }
        label = str(_pick(item, "block_label", "label", "type", default="text")).lower()
        content = cleanup_german_ocr(str(_pick(item, "block_content", "content", "text", default="") or ""))
        bbox = _bbox(_pick(item, "block_bbox", "bbox", "coordinate", default=[]))
        if parse_printed_page_number(content) is not None and is_margin_page_hint(bbox, page, content):
            page.printed_page = parse_printed_page_number(content)
        if label in DROP_LABELS:
            continue
        image_path = None
        if label in {"image", "figure", "chart", "table"}:
            crop = _crop_block(page, bbox, image_dir, index)
            if crop:
                image_path = crop
            elif label in {"image", "figure"} and not content:
                continue
        blocks.append(
            Block(
                label=label,
                content=content,
                bbox=bbox,
                order=item.get("block_order", index) if isinstance(item, dict) else index,
                image_path=image_path,
            )
        )
    rec_texts = _as_sequence(_pick(data, "rec_texts", "rec_text", default=[]))
    rec_boxes = _as_sequence(_pick(data, "rec_boxes", "rec_polys", default=[]))
    if not blocks and rec_texts:
        for index, text in enumerate(rec_texts):
            box = rec_boxes[index] if index < len(rec_boxes) else []
            cleaned = cleanup_german_ocr(str(text))
            if not cleaned:
                continue
            blocks.append(Block(label="text", content=cleaned, bbox=_bbox(box), order=index))
    return blocks


def _is_scanner_bar(crop: Image.Image, bbox: list[float], page: PageResult) -> bool:
    hist = crop.convert("L").histogram()
    total = sum(hist)
    if total <= 0:
        return True
    mean = sum(index * count for index, count in enumerate(hist)) / total
    width, height = crop.size
    aspect = height / max(width, 1)
    if mean < 42:
        return True
    if aspect >= 3.0 and mean < 95:
        return True
    if page.width and len(bbox) >= 4:
        band = bbox[2] - bbox[0]
        if band <= page.width * 0.24 and aspect >= 2.6 and mean < 115:
            return True
    return False


def _crop_block(page: PageResult, bbox: list[float], dest: Path, block_index: int) -> str | None:
    if len(bbox) < 4:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    with Image.open(page.image_path) as image:
        x1, y1, x2, y2 = [int(max(0, v)) for v in bbox[:4]]
        x2 = min(image.width, max(x1 + 1, x2))
        y2 = min(image.height, max(y1 + 1, y2))
        crop = image.crop((x1, y1, x2, y2))
        if crop.width < 12 or crop.height < 12:
            return None
        if _is_scanner_bar(crop, bbox, page):
            return None
        out = dest / f"p{page.index:04d}_b{block_index:03d}.jpg"
        crop.convert("RGB").save(out, "JPEG", quality=88)
        return str(out)


def _merge_line_blocks(blocks: list[Block], page: PageResult) -> list[Block]:
    """Join OCR lines into flowing paragraphs; keep real headings separate."""
    merged: list[Block] = []
    body_lines: list[str] = []
    body_bbox: list[float] = []

    def flush_body() -> None:
        nonlocal body_lines, body_bbox
        if not body_lines:
            return
        merged.append(
            Block(
                label="text",
                content="\n".join(body_lines),
                bbox=body_bbox,
                order=len(merged),
            )
        )
        body_lines = []
        body_bbox = []

    for block in blocks:
        if is_markdown_heading(block, page) and block.content:
            flush_body()
            merged.append(block.model_copy(update={"order": len(merged)}))
            continue
        if block.content:
            line = block.content
            if body_lines and body_lines[-1].endswith("-"):
                body_lines[-1] = body_lines[-1][:-1] + line
            else:
                body_lines.append(line)
            if len(block.bbox) >= 4 and not body_bbox:
                body_bbox = list(block.bbox)
            elif len(block.bbox) >= 4 and body_bbox:
                body_bbox[2] = max(body_bbox[2], block.bbox[2])
                body_bbox[3] = max(body_bbox[3], block.bbox[3])
    flush_body()
    return merged or blocks


def _markdown_from_blocks(blocks: list[Block], page: PageResult) -> str:
    lines: list[str] = []
    for block in blocks:
        if is_markdown_heading(block, page):
            prefix = "#" if block.label == "doc_title" else "##"
            if block.content:
                lines.append(f"{prefix} {block.content}")
        elif block.image_path:
            lines.append(f"![Abbildung]({block.image_path})")
            if block.content:
                lines.append(f"*{block.content}*")
        elif block.label == "table" and block.content:
            lines.append(block.content)
        elif block.content:
            lines.append(block.content)
        lines.append("")
    return "\n".join(lines).strip()


def _json_default(obj: Any):
    if hasattr(obj, "tolist"):
        return obj.tolist()
    return str(obj)


def _predict(pipeline: Any, image_path: str, options: JobOptions):
    if options.ocr_mode == "quality":
        predict_kwargs = {
            "use_doc_unwarping": False,
            "use_doc_orientation_classify": False,
            "use_queues": False,
        }
    else:
        predict_kwargs = {
            "use_doc_unwarping": True,
            "use_doc_orientation_classify": True,
        }
    try:
        return pipeline.predict(image_path, **predict_kwargs)
    except TypeError:
        return pipeline.predict(image_path)


def ocr_page(page: PageResult, options: JobOptions, image_dir: Path) -> PageResult:
    if page.skipped:
        page.markdown = ""
        page.blocks = []
        return page
    pipeline = _get_pipeline(options)
    try:
        raw = _predict(pipeline, page.image_path, options)
    except Exception as exc:
        if options.ocr_mode != "quality":
            raise
        logger.warning(
            "Qualitäts-OCR fehlgeschlagen (%s), Pipeline wird neu geladen (GPU-Cache bleibt aktiv) …",
            exc,
        )
        _evict_pipeline_for_options(options)
        pipeline = _get_pipeline(options)
        raw = _predict(pipeline, page.image_path, options)
    if not isinstance(raw, list):
        raw = list(raw)
    raw = raw[0] if raw else {}
    data = _as_dict(raw)
    if not data and hasattr(raw, "keys"):
        try:
            data = dict(raw)
        except Exception:
            data = {}
    if not data and hasattr(raw, "__dict__"):
        data = {k: v for k, v in vars(raw).items() if not k.startswith("_")}
    page.blocks = _extract_blocks(data, page, image_dir)
    page.blocks = _sort_blocks_for_reading(page.blocks)
    _fix_reading_order(page)
    page.blocks = _merge_line_blocks(page.blocks, page)
    _fix_reading_order_merged(page)
    markdown = ""
    md_attr = getattr(raw, "markdown", None)
    if isinstance(md_attr, str):
        markdown = md_attr
    elif isinstance(md_attr, dict):
        markdown = str(md_attr.get("markdown") or md_attr.get("text") or md_attr.get("markdown_text") or "")
    elif isinstance(data.get("markdown"), str):
        markdown = data["markdown"]
    elif isinstance(data.get("markdown"), dict):
        markdown = str(data["markdown"].get("markdown") or data["markdown"].get("text") or "")
    from_blocks = _markdown_from_blocks(page.blocks, page)
    page.markdown = from_blocks or markdown.strip()
    json_path = image_dir.parent / "ocr" / f"{page.index:04d}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(page.model_dump(), ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    md_path = json_path.with_suffix(".md")
    md_path.write_text(page.markdown, encoding="utf-8")
    return page
