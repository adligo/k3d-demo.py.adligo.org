"""Bounded-context detection for handwritten math images.

Finds rectangular regions where all ink strokes are self-contained (no lines
traverse out of the bounds) and returns them as cropped sub-images.

Pipeline:
  1. Binarize — grayscale + threshold at 128
  2. Connected components — scipy.ndimage.label with 8-connectivity
  3. Filter noise — discard components with < 5 pixels
  4. Bounding boxes — scipy.ndimage.find_objects
  5. Adaptive gap threshold — max(width * 0.03, median_comp_width * 0.5)
     scaled by depth for recursive segmentation
  6. Merge by X-overlap — sort by x_min, merge if gap < threshold
  7. Crop — extract each bounded context with off-canvas padding
  8. Fraction bar detection — fallback vertical split for fractions
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image
from scipy.ndimage import label, find_objects


class BoundingBox(NamedTuple):
    """Axis-aligned bounding box for a connected region."""
    x_min: int
    y_min: int
    x_max: int
    y_max: int
    pixel_count: int


# 8-connectivity structuring element
_STRUCT_8 = np.ones((3, 3), dtype=np.int32)

MIN_COMPONENT_PIXELS = 5


def _binarize(image: Image.Image) -> np.ndarray:
    """Convert to grayscale and threshold to a binary ink mask.

    Returns a boolean array where True = ink pixel.
    """
    gray = np.array(image.convert("L"))
    return gray < 128


def _connected_components(binary: np.ndarray) -> tuple[np.ndarray, int]:
    """Label connected components using 8-connectivity."""
    return label(binary, structure=_STRUCT_8)


def _extract_boxes(
    labeled: np.ndarray, num_components: int, binary: np.ndarray
) -> list[BoundingBox]:
    """Extract bounding boxes for each component, filtering noise."""
    slices = find_objects(labeled)
    boxes: list[BoundingBox] = []
    for i, slc in enumerate(slices, start=1):
        if slc is None:
            continue
        mask = labeled[slc] == i
        pixel_count = int(mask.sum())
        if pixel_count < MIN_COMPONENT_PIXELS:
            continue
        y_slice, x_slice = slc
        boxes.append(BoundingBox(
            x_min=x_slice.start,
            y_min=y_slice.start,
            x_max=x_slice.stop,
            y_max=y_slice.stop,
            pixel_count=pixel_count,
        ))
    return boxes


def _compute_gap_threshold(
    image_width: int, boxes: list[BoundingBox], depth: int = 0
) -> float:
    """Adaptive gap threshold for merging nearby components.

    Base: max(image_width * 0.03, median_component_width * 0.5)
    Scaled by depth: multiplied by max(0.3, 1.0 - depth * 0.25) so deeper
    levels use progressively tighter merging.
    """
    if not boxes:
        return image_width * 0.03
    widths = sorted(b.x_max - b.x_min for b in boxes)
    median_w = widths[len(widths) // 2]
    base = max(image_width * 0.03, median_w * 0.5)
    scale = max(0.3, 1.0 - depth * 0.25)
    return base * scale


def _merge_boxes(boxes: list[BoundingBox], gap_threshold: float) -> list[BoundingBox]:
    """Sort by x_min and merge boxes whose horizontal gap < threshold."""
    if not boxes:
        return []
    sorted_boxes = sorted(boxes, key=lambda b: b.x_min)
    merged: list[list[int]] = []  # [x_min, y_min, x_max, y_max, pixel_count]

    cur = list(sorted_boxes[0])
    for box in sorted_boxes[1:]:
        gap = box.x_min - cur[2]  # box.x_min - current x_max
        if gap < gap_threshold:
            # Merge: expand the bounding box
            cur[0] = min(cur[0], box.x_min)
            cur[1] = min(cur[1], box.y_min)
            cur[2] = max(cur[2], box.x_max)
            cur[3] = max(cur[3], box.y_max)
            cur[4] += box.pixel_count
        else:
            merged.append(cur)
            cur = list(box)
    merged.append(cur)

    return [BoundingBox(*m) for m in merged]


def _detect_fraction_bar(binary: np.ndarray) -> int | None:
    """Detect a horizontal fraction bar in a binary image.

    Looks for rows where ink spans >60% of image width, with ink both
    above and below that row. Returns the row index of the bar, or None.
    """
    h, w = binary.shape
    if h < 5 or w < 5:
        return None

    # Find rows where ink covers >60% of width
    row_ink = binary.sum(axis=1)
    threshold = w * 0.6

    candidate_rows = np.where(row_ink > threshold)[0]
    if len(candidate_rows) == 0:
        return None

    # Pick the middle candidate row
    bar_row = int(candidate_rows[len(candidate_rows) // 2])

    # Must have ink above and below
    ink_above = binary[:bar_row].any() if bar_row > 0 else False
    ink_below = binary[bar_row + 1:].any() if bar_row < h - 1 else False

    if ink_above and ink_below:
        return bar_row
    return None


def _split_at_fraction_bar(
    image: Image.Image, bar_row: int, padding: int = 3
) -> list[BoundingBox]:
    """Split an image at a fraction bar into numerator and denominator boxes."""
    w, h = image.size
    binary = _binarize(image)

    # Numerator: everything above the bar
    num_region = binary[:max(0, bar_row - padding)]
    if num_region.any():
        rows_with_ink = np.where(num_region.any(axis=1))[0]
        cols_with_ink = np.where(num_region.any(axis=0))[0]
        num_box = BoundingBox(
            x_min=int(cols_with_ink[0]),
            y_min=int(rows_with_ink[0]),
            x_max=int(cols_with_ink[-1]) + 1,
            y_max=int(rows_with_ink[-1]) + 1,
            pixel_count=int(num_region.sum()),
        )
    else:
        num_box = None

    # Denominator: everything below the bar
    den_start = min(h, bar_row + padding + 1)
    den_region = binary[den_start:]
    if den_region.any():
        rows_with_ink = np.where(den_region.any(axis=1))[0]
        cols_with_ink = np.where(den_region.any(axis=0))[0]
        den_box = BoundingBox(
            x_min=int(cols_with_ink[0]),
            y_min=den_start + int(rows_with_ink[0]),
            x_max=int(cols_with_ink[-1]) + 1,
            y_max=den_start + int(rows_with_ink[-1]) + 1,
            pixel_count=int(den_region.sum()),
        )
    else:
        den_box = None

    boxes = []
    if num_box is not None:
        boxes.append(num_box)
    if den_box is not None:
        boxes.append(den_box)
    return boxes


def segment_image(image_path: str | Path) -> list[BoundingBox]:
    """Find bounded contexts in a math formula image.

    Returns a list of BoundingBox sorted left-to-right.
    """
    image = Image.open(image_path).convert("RGB")
    binary = _binarize(image)
    labeled, num_components = _connected_components(binary)
    boxes = _extract_boxes(labeled, num_components, binary)
    gap_threshold = _compute_gap_threshold(image.width, boxes)
    merged = _merge_boxes(boxes, gap_threshold)
    return merged


def segment_pil_image(
    image: Image.Image, depth: int = 0
) -> list[BoundingBox]:
    """Find bounded contexts in an in-memory PIL image.

    Like ``segment_image`` but operates on a PIL Image directly.
    Uses depth-scaled gap threshold for recursive segmentation.

    Returns a list of BoundingBox sorted left-to-right.  If horizontal
    segmentation yields ≤1 region, tries fraction-bar splitting as fallback.
    """
    binary = _binarize(image)
    labeled, num_components = _connected_components(binary)
    boxes = _extract_boxes(labeled, num_components, binary)
    gap_threshold = _compute_gap_threshold(image.width, boxes, depth=depth)
    merged = _merge_boxes(boxes, gap_threshold)

    # If we got ≤1 horizontal region, try fraction bar splitting
    if len(merged) <= 1:
        bar_row = _detect_fraction_bar(binary)
        if bar_row is not None:
            frac_boxes = _split_at_fraction_bar(image, bar_row)
            if len(frac_boxes) >= 2:
                return frac_boxes

    return merged


def crop_contexts(
    image: Image.Image, contexts: list[BoundingBox], padding: int = 5
) -> list[Image.Image]:
    """Crop each bounded context from the original image with off-canvas padding.

    Creates a white-background canvas and pastes the image region onto it,
    allowing bounded contexts to extend beyond image edges.

    Returns a list of PIL Images in the same order as *contexts*.
    """
    w, h = image.size
    crops: list[Image.Image] = []
    for box in contexts:
        # Desired region with padding (may extend beyond image bounds)
        x0 = box.x_min - padding
        y0 = box.y_min - padding
        x1 = box.x_max + padding
        y1 = box.y_max + padding

        canvas_w = x1 - x0
        canvas_h = y1 - y0

        # Create white canvas
        canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))

        # Compute source region clipped to image bounds
        src_x0 = max(0, x0)
        src_y0 = max(0, y0)
        src_x1 = min(w, x1)
        src_y1 = min(h, y1)

        # Compute paste offset on canvas
        paste_x = src_x0 - x0
        paste_y = src_y0 - y0

        # Crop from source and paste onto canvas
        src_crop = image.crop((src_x0, src_y0, src_x1, src_y1))
        canvas.paste(src_crop, (paste_x, paste_y))

        crops.append(canvas)
    return crops
