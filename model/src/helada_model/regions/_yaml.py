"""Minimal reader for the YAML subset used by region configs (no PyYAML dependency).

Supported: block mappings, block lists (of scalars or of mappings), inline lists of scalars `[a, "b", 1.5]`,
quoted/plain scalars, numbers, true/false, null/~, and `#` comments. Not supported: flow mappings `{}`,
multi-line scalars (`|`, `>`), anchors, tags. If PyYAML is installed, `load_text` uses it instead.
"""
from __future__ import annotations

import re

_NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


def _strip_comment(line: str) -> str:
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _split_inline(s: str) -> list[str]:
    parts, cur, q = [], "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch; cur += ch
        elif ch == ",":
            parts.append(cur.strip()); cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def scalar(s: str):
    s = s.strip()
    if s == "" or s in ("null", "~", "Null", "NULL"):
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        return [scalar(x) for x in _split_inline(s[1:-1])]
    if s.startswith("{"):
        raise ValueError(f"flow mappings are not supported by the region config reader: {s!r}")
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if _NUM.match(s):
        return int(s) if re.match(r"^[-+]?\d+$", s) else float(s)
    return s


def _split_key(s: str):
    """'key: value' -> (key, value-or-''), or None if s is not a mapping entry."""
    m = re.match(r"^([A-Za-z0-9_\-\.]+|\"[^\"]*\"|'[^']*'):(\s+|$)(.*)$", s)
    if not m:
        return None
    k = m.group(1)
    if k[0] in "\"'":
        k = k[1:-1]
    return k, m.group(3)


def _parse_block(lines, i, indent):
    """Parse lines[i:] at exactly `indent`; return (value, next_index)."""
    if i >= len(lines):
        return None, i
    ind, text = lines[i]
    if text.startswith("- ") or text == "-":
        out = []
        while i < len(lines) and lines[i][0] == indent and (lines[i][1].startswith("- ") or lines[i][1] == "-"):
            rest = lines[i][1][2:].strip() if lines[i][1] != "-" else ""
            kv = _split_key(rest) if rest else None
            if rest == "":
                val, i = _parse_block(lines, i + 1, lines[i + 1][0]) if i + 1 < len(lines) and lines[i + 1][0] > indent else (None, i + 1)
                out.append(val)
            elif kv is not None:
                # a mapping item: first key on the dash line, the rest indented at indent + 2
                sub = [(indent + 2, rest)]
                j = i + 1
                while j < len(lines) and lines[j][0] > indent:
                    sub.append(lines[j]); j += 1
                val, _ = _parse_block(sub, 0, indent + 2)
                out.append(val); i = j
            else:
                out.append(scalar(rest)); i += 1
        return out, i
    out = {}
    while i < len(lines) and lines[i][0] == indent:
        text = lines[i][1]
        kv = _split_key(text)
        if kv is None:
            raise ValueError(f"cannot parse config line: {text!r}")
        k, v = kv
        if v.strip() == "":
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                val, i = _parse_block(lines, i + 1, lines[i + 1][0])
            elif i + 1 < len(lines) and lines[i + 1][0] == indent and lines[i + 1][1].startswith("- "):
                val, i = _parse_block(lines, i + 1, indent)   # list at the same indent as its key
            else:
                val, i = None, i + 1
            out[k] = val
        else:
            out[k] = scalar(v); i += 1
    return out, i


def parse(text: str):
    lines = []
    for raw in text.splitlines():
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ValueError("tabs are not allowed for indentation")
        s = _strip_comment(raw)
        if s.strip() == "" or s.strip() == "---":
            continue
        lines.append((len(s) - len(s.lstrip(" ")), s.strip()))
    if not lines:
        return {}
    val, i = _parse_block(lines, 0, lines[0][0])
    if i != len(lines):
        raise ValueError(f"unexpected indentation near: {lines[i][1]!r}")
    return val


def load_text(text: str):
    try:
        import yaml  # type: ignore
    except ImportError:
        return parse(text)
    return yaml.safe_load(text)
