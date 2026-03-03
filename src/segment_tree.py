"""Recursive segment tree for hierarchical formula decomposition.

Builds a tree of ``SegmentNode`` objects by recursively segmenting an image
into sub-regions until no further splits are possible.  Each leaf node
represents an atomic visual element paired with its LaTeX label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from src.segmentation import segment_pil_image, crop_contexts, BoundingBox
from src.label_alignment import split_latex_for_depth, align_segments_to_labels

MAX_RECURSION_DEPTH = 5


@dataclass
class SegmentNode:
    """A node in the recursive segmentation tree."""

    segment_id: str
    image: Image.Image
    bbox: BoundingBox | None
    latex_label: str
    children: list[SegmentNode] = field(default_factory=list)
    depth: int = 0
    disk_path: Path | None = None
    predicted_latex: str | None = None

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def leaves(self) -> list[SegmentNode]:
        """Return all leaf nodes in left-to-right, depth-first order."""
        if self.is_leaf:
            return [self]
        result: list[SegmentNode] = []
        for child in self.children:
            result.extend(child.leaves())
        return result

    def max_depth(self) -> int:
        """Return the maximum depth in this subtree."""
        if self.is_leaf:
            return self.depth
        return max(child.max_depth() for child in self.children)


def recursive_segment(
    image: Image.Image,
    latex_label: str,
    depth: int = 0,
    parent_id: str = "root",
) -> SegmentNode:
    """Recursively segment an image into a tree of SegmentNodes.

    1. Call ``segment_pil_image(image, depth)`` to find sub-regions
    2. If ≤1 sub-region → leaf node (no further splitting)
    3. Otherwise: split the LaTeX label to match, crop sub-images, recurse
    4. Max recursion depth = 5 (safety limit)
    """
    node = SegmentNode(
        segment_id=parent_id,
        image=image,
        bbox=None,
        latex_label=latex_label,
        depth=depth,
    )

    # Safety limit
    if depth >= MAX_RECURSION_DEPTH:
        return node

    # Find sub-regions
    boxes = segment_pil_image(image, depth)

    # If ≤1 region, this is a leaf
    if len(boxes) <= 1:
        return node

    # Crop sub-images
    crops = crop_contexts(image, boxes)

    # Split LaTeX to match number of segments
    latex_parts = split_latex_for_depth(latex_label, depth)
    labels = align_segments_to_labels(len(crops), latex_parts, parent_id)

    # Recurse on each child
    for i, (crop, lbl, box) in enumerate(zip(crops, labels, boxes)):
        child_id = f"segment_{i:02d}"
        child = recursive_segment(crop, lbl, depth + 1, child_id)
        child.segment_id = child_id
        child.bbox = box
        child.depth = depth + 1
        node.children.append(child)

    return node


def save_tree(node: SegmentNode, base_dir: Path) -> None:
    """Save the segment tree to nested directories on disk.

    Leaves become PNG files; internal nodes become directories containing
    their children.

    Example output::

        base_dir/segment_00/segment_00.png   → leaf
        base_dir/segment_00/segment_01.png   → leaf
        base_dir/segment_01.png              → leaf (no children)
    """
    base_dir.mkdir(parents=True, exist_ok=True)

    if node.is_leaf:
        # Root leaf: save directly in base_dir
        if node.segment_id == "root":
            path = base_dir / "segment_00.png"
            node.image.save(path)
            node.disk_path = path
        return

    for child in node.children:
        if child.is_leaf:
            # Save leaf as a PNG file
            path = base_dir / f"{child.segment_id}.png"
            child.image.save(path)
            child.disk_path = path
        else:
            # Save internal node as a directory, recurse
            child_dir = base_dir / child.segment_id
            save_tree(child, child_dir)
            child.disk_path = child_dir
