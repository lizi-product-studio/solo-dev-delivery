"""Pure JSON relation helpers shared by read-only checks and table generation.

A generated table proves correspondence with selected data and row order only.
It does not establish the meaning or correctness of surrounding prose.
"""
from __future__ import annotations

import html
import re
from decimal import Decimal

BLOCK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


def pointer(value, parts):
    """Resolve the kit's list-form pointer without coercing keys or values."""
    if not isinstance(parts, list):
        raise ValueError("JSON pointer must be a list of object keys/list indices")
    for part in parts:
        if isinstance(value, list) and type(part) is int:
            value = value[part]
        elif isinstance(value, dict) and isinstance(part, str):
            value = value[part]
        else:
            raise ValueError("JSON pointer has an incompatible component")
    return value


def relation_pointer(value, parts):
    if (not isinstance(parts, list)
            or any(type(p) not in (str, int) or (type(p) is int and p < 0) for p in parts)):
        raise ValueError("relation pointer must contain object keys or non-negative array indices")
    return pointer(value, parts)


def block_markers(name):
    """Return exact, standalone markers for one named generated table block."""
    if not isinstance(name, str) or not BLOCK_NAME.fullmatch(name):
        raise ValueError("block must be a 1-80 character ASCII name using letters, digits, _, . or -")
    return ("<!-- generated:" + name + ":start -->",
            "<!-- generated:" + name + ":end -->")


def table_block(text, name):
    """Read only one uniquely delimited block; leave all surrounding prose alone."""
    start, end = block_markers(name)
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError("generated block needs exactly one start and one end marker")
    lines = text.replace("\r\n", "\n").split("\n")
    if lines.count(start) != 1 or lines.count(end) != 1:
        raise ValueError("generated block markers must occupy complete lines")
    first, last = lines.index(start), lines.index(end)
    if first >= last:
        raise ValueError("generated block markers are reversed")
    return "\n".join(lines[first + 1:last])


def _cell(value):
    if value is None:
        text = "未知"
    elif isinstance(value, str):
        text = value
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (Decimal, int)):
        number = Decimal(value)
        digits = number.as_tuple()
        if not number.is_finite() or len(digits.digits) > 100 or abs(digits.exponent) > 100:
            raise ValueError("table number must be finite and within supported 100-digit / exponent-100 range")
        # Fixed decimal notation preserves every digit; no float or Decimal context arithmetic.
        text = format(number, "f")
    else:
        # Reject floats as callers may already have lost digits while decoding JSON.
        raise ValueError("table cells must be JSON scalars decoded with exact numbers")
    # Preserve user data as literal cell text; HTML/Markdown must not create cells or rows.
    text = html.escape(text, quote=False)
    text = text.replace("\\", "&#92;").replace("|", "&#124;")
    for char in ("`", "*", "_", "[", "]", "~"):
        text = text.replace(char, "&#" + str(ord(char)) + ";")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def render_table(rows, columns):
    """Render deterministic Markdown from rows and [{label, pointer}] columns.

    JSON numbers must be Decimal/int (parse source JSON with precise_json).
    Null is always rendered as 未知; absent values are errors, never zero.
    The return value has no trailing newline and contains no block markers.
    """
    if not isinstance(rows, list):
        raise ValueError("table rows pointer must select a JSON array")
    if not isinstance(columns, list) or not columns:
        raise ValueError("table columns must be a non-empty list")
    for column in columns:
        if not isinstance(column, dict) or set(column) != {"label", "pointer"}:
            raise ValueError("table columns need exactly label and pointer")
        if not isinstance(column["label"], str) or not column["label"]:
            raise ValueError("table column label must be non-empty text")
        if not isinstance(column["pointer"], list):
            raise ValueError("table column pointer must be a list")
        # Validate the pointer shape even if rows is empty.
        if any(type(p) not in (str, int) or (type(p) is int and p < 0) for p in column["pointer"]):
            raise ValueError("table column pointer must contain keys or non-negative array indices")
    lines = ["| " + " | ".join(_cell(c["label"]) for c in columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(relation_pointer(row, c["pointer"])) for c in columns) + " |")
    return "\n".join(lines)
