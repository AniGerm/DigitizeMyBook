from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..models import PageResult


def _ink_ratio(gray: np.ndarray) -> float:
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return float(np.count_nonzero(binary)) / float(binary.size)


def _binary_ink(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return cv2.medianBlur(binary, 3)


def _text_direction_score(image: np.ndarray) -> tuple[float, float]:
    binary = _binary_ink(image)
    row_var = float(np.var(np.sum(binary > 0, axis=1)))
    col_var = float(np.var(np.sum(binary > 0, axis=0)))
    return row_var, col_var


def _letter_tallness(image: np.ndarray) -> float:
    binary = _binary_ink(image)
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    ratios: list[float] = []
    for index in range(1, count):
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 18 or width < 2 or height < 2:
            continue
        if max(width, height) > 90:
            continue
        ratios.append(height / max(width, 1))
    return float(np.median(ratios)) if ratios else 1.0


def _longest_run(mask: np.ndarray) -> tuple[int, int]:
    best_start = 0
    best_end = 0
    start: int | None = None
    length = int(mask.shape[0])
    for index, inside in enumerate(mask.tolist()):
        if inside:
            if start is None:
                start = index
        elif start is not None:
            if index - start > best_end - best_start:
                best_start, best_end = start, index
            start = None
    if start is not None and length - start > best_end - best_start:
        best_start, best_end = start, length
    return best_start, best_end


def _trim_axis(profile: np.ndarray, max_cut: int, threshold: float = 0.52) -> tuple[int, int]:
    length = int(profile.shape[0])
    if length < 16:
        return 0, length
    kernel = max(5, length // 80)
    if kernel % 2 == 0:
        kernel += 1
    smooth = cv2.GaussianBlur(profile.astype(np.float32).reshape(-1, 1), (1, kernel), 0).ravel()
    start, end = _longest_run(smooth >= threshold)
    if end - start < length * 0.42:
        return 0, length
    return min(start, max_cut), max(end, length - max_cut)


def _trim_black_bars(image: np.ndarray, max_frac: float = 0.22) -> np.ndarray:
    """Remove only pure-black scanner-bed strips, keep binding shadows and margins."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height, width = gray.shape
    if height < 40 or width < 40:
        return image
    max_cut = max(8, int(max(height, width) * max_frac))

    def black_edge(line: np.ndarray) -> bool:
        return float(np.mean(line < 42)) >= 0.72

    x0, x1 = 0, width
    y0, y1 = 0, height
    while x0 < max_cut and black_edge(gray[:, x0]):
        x0 += 1
    while width - x1 < max_cut and black_edge(gray[:, x1 - 1]):
        x1 -= 1
    while y0 < max_cut and black_edge(gray[y0, :]):
        y0 += 1
    while height - y1 < max_cut and black_edge(gray[y1 - 1, :]):
        y1 -= 1
    cropped = image[y0:y1, x0:x1]
    if cropped.size == 0 or cropped.shape[0] < 32 or cropped.shape[1] < 32:
        return image
    return cropped


def _trim_to_paper(image: np.ndarray, max_frac: float = 0.32) -> np.ndarray:
    """Drop scanner-bed bars and dark cover edges; ignore dust specks in the black."""
    trimmed = _trim_black_bars(image, max_frac=max_frac)
    if not np.array_equal(trimmed, image):
        return trimmed
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height, width = gray.shape
    if height < 40 or width < 40:
        return image
    paper = gray > 148
    x0, x1 = _trim_axis(paper.mean(axis=0), int(width * max_frac))
    y0, y1 = _trim_axis(paper.mean(axis=1), int(height * max_frac))
    cropped = image[y0:y1, x0:x1]
    if cropped.size == 0 or cropped.shape[0] < 32 or cropped.shape[1] < 32:
        return image
    return cropped


def _crop_scanner_bed(image: np.ndarray) -> np.ndarray:
    return _trim_black_bars(image)


def _edge_means(image: np.ndarray) -> tuple[float, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height, width = gray.shape
    band = max(14, width // 12)
    y0, y1 = int(height * 0.1), int(height * 0.9)
    mid = gray[y0:y1, band : width - band]
    left = gray[y0:y1, :band]
    right = gray[y0:y1, width - band :]
    paper = float(np.mean(mid)) if mid.size else 200.0
    return float(np.mean(left) - paper), float(np.mean(right) - paper)


def order_spread_halves(first: np.ndarray, second: np.ndarray) -> list[np.ndarray]:
    """Left book page has the dark outer edge on the left; right page on the right."""
    left_a, right_a = _edge_means(first)
    left_b, right_b = _edge_means(second)
    first_is_right = right_a < left_a - 10 and right_a < -12
    second_is_left = left_b < right_b - 10 and left_b < -12
    first_is_left = left_a < right_a - 10 and left_a < -12
    second_is_right = right_b < left_b - 10 and right_b < -12
    if first_is_right and second_is_left and not (first_is_left and second_is_right):
        return [second, first]
    return [first, second]


def reorder_spread_pages(pages: list[PageResult]) -> list[PageResult]:
    """After OCR, swap split halves whose printed page numbers are reversed."""
    if len(pages) < 2:
        return pages
    groups: dict[int, list[PageResult]] = {}
    for page in pages:
        key = page.spread_id if page.spread_id is not None else page.index
        groups.setdefault(key, []).append(page)
    ordered: list[PageResult] = []
    for key in sorted(groups):
        group = groups[key]
        if len(group) == 2:
            first, second = group[0], group[1]
            a, b = first.printed_page, second.printed_page
            if a is not None and b is not None and a > b:
                group = [second, first]
        ordered.extend(group)
    for index, page in enumerate(ordered):
        page.index = index
    return ordered


def sort_pages_by_printed_page(pages: list[PageResult]) -> list[PageResult]:
    """Reorder uploaded pages when enough printed page numbers were detected."""
    active = [page for page in pages if not page.skipped]
    numbered = [page for page in active if page.printed_page is not None]
    if len(numbered) < max(2, len(active) // 2):
        return pages
    unnumbered = [page for page in active if page.printed_page is None]
    skipped = [page for page in pages if page.skipped]
    numbered.sort(key=lambda page: (page.printed_page or 0, page.index))
    ordered = numbered + unnumbered + skipped
    for index, page in enumerate(ordered):
        page.index = index
    return ordered


def _title_at_top_score(image: np.ndarray) -> float:
    binary = _binary_ink(image)
    _count, _labels, stats, centroids = cv2.connectedComponentsWithStats(binary, 8)
    height = image.shape[0]
    score = 0.0
    for index in range(1, _count):
        area = float(stats[index, cv2.CC_STAT_AREA])
        if area < 80:
            continue
        center_y = float(centroids[index][1])
        if center_y < height * 0.38:
            score += area
        elif center_y > height * 0.68:
            score -= area * 0.4
    return score


def _diacritic_upright_score(image: np.ndarray) -> float:
    """Positive if i-dots/umlauts sit above letters (upright Latin/German)."""
    binary = _binary_ink(image)
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(binary, 8)
    letters: list[tuple[float, int, int, int, int]] = []
    dots: list[tuple[float, float]] = []
    for index in range(1, count):
        x = int(stats[index, cv2.CC_STAT_LEFT])
        y = int(stats[index, cv2.CC_STAT_TOP])
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        area = int(stats[index, cv2.CC_STAT_AREA])
        cx = float(centroids[index][0])
        cy = float(centroids[index][1])
        if area < 6:
            continue
        if 8 <= height <= 55 and 3 <= width <= 45 and area >= 18:
            letters.append((cx, y, height, x, width))
        elif area <= 45 and height <= 12 and width <= 12:
            dots.append((cx, cy))
    if len(dots) < 6 or not letters:
        return 0.0
    above = 0
    below = 0
    for cx, cy in dots:
        best: tuple[float, int] | None = None
        for _lx, top, height, left, width in letters:
            if cx < left - 3 or cx > left + width + 3:
                continue
            if cy < top:
                gap, side = top - cy, -1
            elif cy > top + height:
                gap, side = cy - (top + height), 1
            else:
                continue
            if gap < height * 0.8 and (best is None or gap < best[0]):
                best = (gap, side)
        if best is None:
            continue
        if best[1] < 0:
            above += 1
        else:
            below += 1
    total = above + below
    if total < 6:
        return 0.0
    return (above - below) / total


def _correct_180(image: np.ndarray) -> np.ndarray:
    flipped = cv2.rotate(image, cv2.ROTATE_180)
    if _diacritic_upright_score(flipped) > _diacritic_upright_score(image) + 0.12:
        return flipped
    return image


def make_upright(image: np.ndarray) -> np.ndarray:
    """Rotate sideways scans upright; avoid flipping normal portrait book pages (A4-like ratio)."""
    image = _crop_scanner_bed(image)
    height, width = image.shape[:2]
    tallness = _letter_tallness(image)
    row_var, col_var = _text_direction_score(image)
    aspect = height / max(width, 1)
    if aspect > 1.12 and (aspect < 1.55 or col_var <= row_var * 1.5):
        return _correct_180(image)
    if tallness < 0.9 or col_var > row_var * 1.15:
        candidates = [
            cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE),
            cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE),
        ]
        image = max(candidates, key=lambda im: (_letter_tallness(im), _diacritic_upright_score(im)))
    return _correct_180(image)


def _gutter_index(profile: np.ndarray, length: int) -> int | None:
    if length < 80:
        return None
    kernel = max(5, length // 40)
    if kernel % 2 == 0:
        kernel += 1
    column = profile.astype(np.float32).reshape(-1, 1)
    smooth = cv2.GaussianBlur(column, (1, kernel), 0).ravel()
    lo, hi = int(length * 0.38), int(length * 0.62)
    if hi <= lo:
        return None
    region = smooth[lo:hi]
    idx = lo + int(np.argmin(region))
    median = float(np.median(smooth))
    if median <= 1e-6:
        return None
    if float(smooth[idx]) > median * 0.55:
        return None
    return idx


def is_likely_spread(image: np.ndarray) -> bool:
    height, width = image.shape[:2]
    return width >= height * 1.25


def split_spread(image: np.ndarray) -> list[np.ndarray]:
    height, width = image.shape[:2]
    if not is_likely_spread(image):
        return [image]

    binary = _binary_ink(image)
    col_profile = np.mean(binary > 0, axis=0)
    vertical = _gutter_index(col_profile, width)
    if vertical is not None:
        mid = max(8, min(width - 8, vertical))
        return [image[:, :mid], image[:, mid:]]
    if width >= height * 1.45:
        mid = width // 2
        return [image[:, :mid], image[:, mid:]]
    return [image]


def _deskew(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    inverted = cv2.bitwise_not(gray)
    thresh = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if coords.size < 50:
        return image
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.3 or abs(angle) > 15:
        return image
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _whiten_paper(image: np.ndarray) -> np.ndarray:
    """Lift the page background toward white without turning photos into pure black and white."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    if float(np.std(gray)) < 4.0:
        return image
    height, width = gray.shape[:2]
    kernel = max(31, (min(height, width) // 12) | 1)
    background = cv2.GaussianBlur(gray, (kernel, kernel), 0)
    background = np.maximum(background.astype(np.float32), 1.0)
    scale = np.clip(255.0 / background, 0.55, 3.2)
    lifted = np.clip(image.astype(np.float32) * scale[..., None], 0, 255).astype(np.uint8)
    gray_out = cv2.cvtColor(lifted, cv2.COLOR_BGR2GRAY)
    dark = gray_out < 90
    if int(np.count_nonzero(dark)) > 40:
        stretched = cv2.normalize(gray_out, None, 0, 255, cv2.NORM_MINMAX)
        ink = stretched < 140
        boosted = lifted.copy()
        boosted[ink] = (boosted[ink].astype(np.float32) * 0.72).astype(np.uint8)
        return boosted
    return lifted


def _line_baselines(gray: np.ndarray) -> list[np.ndarray]:
    """One y(x) curve per text line, top to bottom. Empty when the page has too little text."""
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _threshold, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    height, width = binary.shape
    kernel_w = max(25, width // 28)
    closed = cv2.morphologyEx(
        binary,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 3)),
    )
    contours, _hierarchy = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    lines: list[np.ndarray] = []
    for contour in contours:
        x, y, box_w, box_h = cv2.boundingRect(contour)
        if box_w < width * 0.32 or box_h < 2 or box_h > height * 0.08 or box_w < box_h * 4:
            continue
        mask = np.zeros((height, width), np.uint8)
        cv2.drawContours(mask, [contour], -1, 255, thickness=-1)
        region = mask[y : y + box_h, x : x + box_w]
        ys = np.full(width, np.nan, np.float32)
        for local_x in range(box_w):
            ink = np.flatnonzero(region[:, local_x])
            if ink.size:
                ys[x + local_x] = float(y + int(ink[-1]))
        known = np.flatnonzero(~np.isnan(ys))
        if known.size < 8:
            continue
        lines.append(np.interp(np.arange(width), known, ys[known]).astype(np.float32))
    lines.sort(key=lambda row: float(np.median(row)))
    kept: list[np.ndarray] = []
    for row in lines:
        if kept and abs(float(np.median(row)) - float(np.median(kept[-1]))) < 8:
            continue
        kept.append(row)
    return kept


def _flatten_baselines(image: np.ndarray) -> np.ndarray:
    """Warp the page so detected text baselines become straight horizontal lines."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height, width = gray.shape[:2]
    lines = _line_baselines(gray)
    if len(lines) < 4:
        return image
    source_y = np.stack(lines, axis=0)
    if float(np.max(np.std(source_y, axis=1))) < 1.6:
        return image
    target_y = np.median(source_y, axis=1).astype(np.float32)
    order = np.argsort(target_y)
    control_y = target_y[order]
    control_shift = (source_y - target_y[:, None])[order]
    sample_y = np.arange(0, height, 4, dtype=np.float32)
    if sample_y.size == 0 or sample_y[-1] != height - 1:
        sample_y = np.append(sample_y, height - 1)
    shift = np.zeros((sample_y.size, width), np.float32)
    for index, y_pos in enumerate(sample_y):
        if y_pos <= control_y[0]:
            shift[index] = control_shift[0]
            continue
        if y_pos >= control_y[-1]:
            shift[index] = control_shift[-1]
            continue
        upper = int(np.searchsorted(control_y, y_pos, side="right"))
        upper = min(max(upper, 1), len(control_y) - 1)
        span = max(float(control_y[upper] - control_y[upper - 1]), 1e-3)
        blend = float(np.clip((y_pos - control_y[upper - 1]) / span, 0.0, 1.0))
        shift[index] = control_shift[upper - 1] * (1.0 - blend) + control_shift[upper] * blend
    full_shift = cv2.resize(shift, (width, height), interpolation=cv2.INTER_LINEAR)
    map_x = np.tile(np.arange(width, dtype=np.float32), (height, 1))
    map_y = np.arange(height, dtype=np.float32)[:, None] + full_shift
    return cv2.remap(image, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def preprocess_pages(
    pages: list[PageResult],
    dest_dir: Path,
    *,
    enable: bool = True,
    split_spreads: bool = False,
    skip_blank: bool = True,
    start_index: int = 0,
) -> list[PageResult]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    processed: list[PageResult] = []
    next_index = start_index

    for page in pages:
        image = cv2.imread(page.image_path)
        if image is None:
            pil = Image.open(page.image_path).convert("RGB")
            image = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

        image = make_upright(image)
        variants = split_spread(image) if split_spreads else [image]
        variants = [_correct_180(variant) for variant in variants]
        if len(variants) == 2:
            variants = order_spread_halves(variants[0], variants[1])
        for variant in variants:
            work = _trim_black_bars(variant)
            if enable:
                work = _deskew(work)
                work = _whiten_paper(work)
                work = _flatten_baselines(work)
                work = _trim_black_bars(work, max_frac=0.12)
            gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
            ink = _ink_ratio(gray)
            mean = float(np.mean(gray))
            spread = float(np.std(gray))
            uniform_blank = spread < 3.0 and (mean < 12.0 or mean > 248.0)
            skipped = skip_blank and (uniform_blank or ink < 0.004)
            out = dest_dir / f"{next_index:04d}.png"
            cv2.imwrite(str(out), work)
            processed.append(
                PageResult(
                    index=next_index,
                    source_path=page.source_path,
                    image_path=str(out),
                    width=int(work.shape[1]),
                    height=int(work.shape[0]),
                    skipped=skipped,
                    skip_reason="leere Seite" if skipped else None,
                    spread_id=page.index,
                    source_index=page.source_index if page.source_index is not None else page.index,
                )
            )
            next_index += 1
    return processed
