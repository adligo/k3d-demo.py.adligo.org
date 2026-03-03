"""LaTeX splitting and segment-to-label alignment.

Splits a LaTeX formula at top-level ``=`` and ``+`` operators (those not
inside ``{}`` braces), then aligns the resulting parts to the N image
segments detected by the segmentation pipeline.

Multi-level splitting:
  - Depth 0: split at top-level ``=`` and ``+``
  - Depth 1: split at ``\\frac{num}{den}``, ``\\mid``, ``\\pm``, ``\\,``
  - Depth 2+: tokenize into individual LaTeX tokens
"""

from __future__ import annotations

import re


def split_latex_top_level(latex: str) -> list[str]:
    """Split a LaTeX string at top-level ``=`` and ``+`` operators.

    Operators that appear inside ``{...}`` are *not* split points.
    Each operator becomes its own element in the output list.

    Example::

        >>> split_latex_top_level("a^{2} + b^{2} = c^{2}")
        ['a^{2}', '+', 'b^{2}', '=', 'c^{2}']
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0

    for ch in latex:
        if ch == '{':
            depth += 1
            current.append(ch)
        elif ch == '}':
            depth -= 1
            current.append(ch)
        elif depth == 0 and ch in ('=', '+'):
            # Flush accumulated text as a part
            text = "".join(current).strip()
            if text:
                parts.append(text)
            # The operator itself is a separate part
            parts.append(ch)
            current = []
        else:
            current.append(ch)

    # Flush remaining text
    text = "".join(current).strip()
    if text:
        parts.append(text)

    return parts


def split_latex_level1(latex: str) -> list[str]:
    r"""Split LaTeX at level-1 boundaries.

    Handles:
    - ``\frac{num}{den}`` → [num, den]
    - ``\mid`` separator
    - ``\pm`` separator
    - ``\,`` thin-space separator
    - ``\left(...\right)`` groups as atomic units

    Returns the split parts, or [latex] if no split is possible.
    """
    stripped = latex.strip()

    # Try \frac{...}{...} — split into numerator and denominator
    frac_match = re.match(r'^\\frac\s*(\{.*)', stripped)
    if frac_match:
        rest = frac_match.group(1)
        # Extract first brace group (numerator)
        num, after_num = _extract_brace_group(rest)
        if num is not None and after_num:
            # Extract second brace group (denominator)
            den, after_den = _extract_brace_group(after_num.lstrip())
            if den is not None:
                parts = [num, den]
                remainder = after_den.strip()
                if remainder:
                    parts.append(remainder)
                return parts

    # Try splitting at \mid, \pm, \, (outside braces)
    for sep in (r'\mid', r'\pm', r'\,'):
        parts = _split_at_command(stripped, sep)
        if len(parts) > 1:
            return parts

    return [stripped] if stripped else []


def split_latex_tokens(latex: str) -> list[str]:
    r"""Tokenize LaTeX into individual tokens.

    Tokens are:
    - ``\command`` sequences (e.g. ``\frac``, ``\sqrt``)
    - Brace groups ``{...}`` (kept as single tokens)
    - Single characters
    - ``^`` and ``_`` with their argument

    Returns the list of tokens, or [latex] if no split.
    """
    stripped = latex.strip()
    if not stripped:
        return []

    tokens: list[str] = []
    i = 0
    while i < len(stripped):
        ch = stripped[i]

        if ch == '\\':
            # LaTeX command: \word or \symbol
            j = i + 1
            if j < len(stripped) and stripped[j].isalpha():
                while j < len(stripped) and stripped[j].isalpha():
                    j += 1
                cmd = stripped[i:j]
                # If followed by brace group, include it
                if j < len(stripped) and stripped[j] == '{':
                    group, rest_pos = _extract_brace_group_pos(stripped, j)
                    if group is not None:
                        tokens.append(cmd + '{' + group + '}')
                        i = rest_pos
                        continue
                tokens.append(cmd)
                i = j
            elif j < len(stripped):
                # Single-char command like \, or \;
                tokens.append(stripped[i:j + 1])
                i = j + 1
            else:
                tokens.append(ch)
                i += 1

        elif ch == '{':
            group, rest_pos = _extract_brace_group_pos(stripped, i)
            if group is not None:
                tokens.append('{' + group + '}')
                i = rest_pos
            else:
                tokens.append(ch)
                i += 1

        elif ch in ('^', '_'):
            # Superscript/subscript with argument
            if i + 1 < len(stripped):
                if stripped[i + 1] == '{':
                    group, rest_pos = _extract_brace_group_pos(stripped, i + 1)
                    if group is not None:
                        tokens.append(ch + '{' + group + '}')
                        i = rest_pos
                        continue
                # Single char argument
                tokens.append(ch + stripped[i + 1])
                i += 2
            else:
                tokens.append(ch)
                i += 1

        elif ch.isspace():
            i += 1

        else:
            tokens.append(ch)
            i += 1

    # Only return split if we got more than 1 token
    if len(tokens) <= 1:
        return [stripped]
    return tokens


def split_latex_for_depth(latex: str, depth: int) -> list[str]:
    """Dispatch LaTeX splitting based on recursion depth.

    - Depth 0: ``split_latex_top_level`` — split at ``=`` and ``+``
    - Depth 1: ``split_latex_level1`` — split fractions, ``\\mid``, ``\\pm``
    - Depth 2+: ``split_latex_tokens`` — individual token splitting
    """
    if depth == 0:
        return split_latex_top_level(latex)
    elif depth == 1:
        return split_latex_level1(latex)
    else:
        return split_latex_tokens(latex)


def align_segments_to_labels(
    num_segments: int,
    latex_parts: list[str],
    image_name: str,
) -> list[str]:
    """Align N image segments to M LaTeX parts left-to-right.

    When counts match, it's a 1:1 mapping.  When they don't, adjacent
    parts are merged (if M > N) or segments share the last label
    (if N > M) to minimise the mismatch.

    Returns a list of *num_segments* LaTeX label strings.
    """
    m = len(latex_parts)

    if num_segments == 0:
        return []

    if m == 0:
        return [""] * num_segments

    if num_segments == m:
        return list(latex_parts)

    if num_segments < m:
        # More parts than segments — merge adjacent parts into segments
        # Distribute parts as evenly as possible across segments
        labels: list[str] = []
        base = m // num_segments
        extra = m % num_segments
        idx = 0
        for i in range(num_segments):
            count = base + (1 if i < extra else 0)
            merged = " ".join(latex_parts[idx:idx + count])
            labels.append(merged)
            idx += count
        return labels

    # num_segments > m — more segments than parts
    # Assign parts to first M segments, last segments get last label repeated
    labels = list(latex_parts)
    while len(labels) < num_segments:
        labels.append(latex_parts[-1])
    return labels


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_brace_group(s: str) -> tuple[str | None, str]:
    """Extract content of the first ``{...}`` group from *s*.

    Returns (content, remainder) or (None, s) if no group found.
    """
    if not s or s[0] != '{':
        return None, s
    depth = 0
    for i, ch in enumerate(s):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return s[1:i], s[i + 1:]
    return None, s


def _extract_brace_group_pos(s: str, start: int) -> tuple[str | None, int]:
    """Extract content of ``{...}`` starting at position *start*.

    Returns (content, position_after_closing_brace) or (None, start).
    """
    if start >= len(s) or s[start] != '{':
        return None, start
    depth = 0
    for i in range(start, len(s)):
        if s[i] == '{':
            depth += 1
        elif s[i] == '}':
            depth -= 1
            if depth == 0:
                return s[start + 1:i], i + 1
    return None, start


def _split_at_command(latex: str, command: str) -> list[str]:
    r"""Split *latex* at occurrences of *command* outside braces.

    The command itself becomes a separate part in the output.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    cmd_len = len(command)

    while i < len(latex):
        ch = latex[i]
        if ch == '{':
            depth += 1
            current.append(ch)
            i += 1
        elif ch == '}':
            depth -= 1
            current.append(ch)
            i += 1
        elif depth == 0 and latex[i:i + cmd_len] == command:
            # Check it's not part of a longer command
            after = i + cmd_len
            if after < len(latex) and latex[after].isalpha():
                current.append(ch)
                i += 1
                continue
            text = "".join(current).strip()
            if text:
                parts.append(text)
            parts.append(command)
            current = []
            i += cmd_len
        else:
            current.append(ch)
            i += 1

    text = "".join(current).strip()
    if text:
        parts.append(text)

    return parts
