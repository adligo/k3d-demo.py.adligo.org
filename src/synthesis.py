"""Additive LaTeX synthesis — bottom-up reconstruction from leaf predictions.

Given a ``SegmentNode`` tree where each leaf has a ``predicted_latex`` value,
``synthesize_latex`` traverses bottom-up and combines the predictions into a
full LaTeX formula.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.segment_tree import SegmentNode


def synthesize_latex(node: SegmentNode) -> str:
    """Reconstruct LaTeX by combining leaf predictions bottom-up.

    Leaves return their ``predicted_latex``.  Internal nodes concatenate
    their children's results with appropriate joining:
    - Depth 0 nodes: space-join children (top-level parts)
    - Fraction splits (2 children from vertical split): wrap as ``\\frac{num}{den}``
    - Other: space-join
    """
    if node.is_leaf:
        return node.predicted_latex or ""

    child_results = [synthesize_latex(child) for child in node.children]

    # Detect if this was a fraction split (2 children, stacked vertically)
    if len(node.children) == 2 and _is_vertical_split(node):
        return f"\\frac{{{child_results[0]}}}{{{child_results[1]}}}"

    # Default: space-join
    return " ".join(child_results)


def _is_vertical_split(node: SegmentNode) -> bool:
    """Check if a node's children represent a vertical (fraction) split.

    True when children are stacked vertically — the second child's bbox
    starts below the first child's bbox.
    """
    if len(node.children) != 2:
        return False
    c0 = node.children[0]
    c1 = node.children[1]
    if c0.bbox is None or c1.bbox is None:
        return False
    # Vertical split: second child starts below first child
    return c1.bbox.y_min > c0.bbox.y_max - 5  # small tolerance


def write_processing_files(
    formula_name: str,
    tree: SegmentNode,
    golden_latex: str,
    processing_dir: Path,
) -> dict:
    """Write per-formula processing results to a JSON file.

    Creates ``processing_dir/<formula_name>.json`` containing:
    - leaf predictions
    - synthesized LaTeX
    - match status vs golden answer

    Returns the result dict.
    """
    processing_dir.mkdir(parents=True, exist_ok=True)

    leaves = tree.leaves()
    leaf_data = []
    for leaf in leaves:
        leaf_data.append({
            "segment_id": leaf.segment_id,
            "depth": leaf.depth,
            "latex_label": leaf.latex_label,
            "predicted_latex": leaf.predicted_latex or "",
            "leaf_match": (leaf.predicted_latex or "").strip() == leaf.latex_label.strip(),
        })

    synthesized = synthesize_latex(tree)
    matches_golden = synthesized.strip() == golden_latex.strip()

    result = {
        "formula_name": formula_name,
        "golden_latex": golden_latex,
        "synthesized_latex": synthesized,
        "matches_golden": matches_golden,
        "num_leaves": len(leaves),
        "max_depth": tree.max_depth(),
        "leaves": leaf_data,
    }

    out_path = processing_dir / f"{formula_name}.json"
    out_path.write_text(json.dumps(result, indent=2))

    return result
