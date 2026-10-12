"""Usage: python3 scripts/check_mermaid_restyle.py --base <ref> <README...>
Minus added whitelisted style lines, the file must equal the baseline byte for
byte (line endings too); inside mermaid blocks only leading indent may change."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# Single source of the subgraph vocabulary: id -> (Chinese title, English title).
VOCAB = {
    "input": ("输入", "Input"), "loop": ("主循环", "Agent Loop"),
    "dispatch": ("分发与校验", "Dispatch and Validation"),
    "tools": ("工具执行", "Tool Execution"),
    "memory": ("记忆与状态", "Memory and State"), "output": ("输出", "Output"),
}
TITLES = {t for pair in VOCAB.values() for t in pair}
LINE = re.compile(r"([^\r\n]*)(\r\n|\r|\n|\Z)")
NAME = r"[A-Za-z_][A-Za-z0-9_]*"
PROP = r"[a-z]+(?:-[a-z]+)*:[#A-Za-z0-9.]+"
CLASSDEF = re.compile(rf"classDef {NAME} {PROP}(?:,{PROP})*")
CLASS = re.compile(rf"class ({NAME}(?:,{NAME})*) {NAME}")
SUB_ID = re.compile(rf'subgraph ({NAME})\["([^"]+)"\]')
LABELS = re.compile(r'"[^"]*"|\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|\|[^|]*\|')
KEYWORDS = {"flowchart", "graph", "LR", "RL", "TD", "TB", "BT", "subgraph",
            "end", "classDef", "class", "style", "linkStyle", "direction"}


class RestyleError(ValueError):
    pass


def split_lines(text: str) -> list[tuple[str, str]]:
    """(body, eol) pairs; eol is '\\r\\n', '\\r', '\\n' or '' (last line)."""
    return [m.groups() for m in LINE.finditer(text) if m.group(0)]


def blocks(lines):
    blk, ids, cur = [], [], None
    for body, _ in lines:
        if cur is not None and body.strip().startswith("```"):
            cur = None
        blk.append(cur)
        if cur is None and body.strip() == "```mermaid":
            cur = len(ids)
            ids.append(set())
        elif cur is not None:
            while LABELS.search(body):
                body = LABELS.sub(" ", body)
            ids[cur] |= set(re.findall(NAME, body)) - KEYWORDS
    return blk, ids


def style_kind(text: str, ids: set[str]) -> str:
    if text in ("", "end") or CLASSDEF.fullmatch(text):
        return text or "blank"
    m = CLASS.fullmatch(text)
    if (m and set(m.group(1).split(",")) <= ids) or text in ids:
        return "class"
    if text.startswith("subgraph ") and text[9:] in TITLES:
        return "subgraph"
    m = SUB_ID.fullmatch(text)
    if m and m.group(2) in VOCAB.get(m.group(1), ()) and m.group(1) not in ids:
        return "subgraph"
    raise RestyleError(f"not an allowed style line: {text!r}")


def check(base_text: str, new_text: str) -> int:
    """Return the number of added style lines; raise RestyleError otherwise."""
    base, new = split_lines(base_text), split_lines(new_text)
    if {e for _, e in base if e} != {e for _, e in new if e}:
        raise RestyleError("line-ending style changed")
    blk, ids = blocks(base)
    i = added = depth = 0
    for n, (body, eol) in enumerate(new):
        ctx = blk[i - 1] if i else None
        old, text = base[i] if i < len(base) else ("", None), body.lstrip(" \t")
        if eol == old[1] and (body == old[0] or blk[i] is not None
                              and text == old[0].lstrip(" \t")):
            if depth and blk[i] is None:
                raise RestyleError(f"line {n + 1}: added subgraph not closed")
            i += 1
            continue
        if ctx is None or eol != new[n - 1][1]:
            raise RestyleError(f"line {n + 1}: changed outside mermaid, or EOL")
        kind = style_kind(text, ids[ctx])
        same = {b.lstrip(" \t") for (b, _), k in zip(base, blk) if k == ctx}
        depth += {"subgraph": 1, "end": -1}.get(kind, 0)
        if depth < 0 or (kind not in ("blank", "end") and text in same):
            raise RestyleError(f"line {n + 1}: extra end or duplicated line")
        added += 1
    if i != len(base) or depth:
        raise RestyleError(f"baseline line {i + 1} missing or changed")
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mermaid restyle byte check")
    parser.add_argument("--base", required=True)
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    failed = 0
    for path in args.paths:
        base = subprocess.run(["git", "show", f"{args.base}:./{path}"],
                              capture_output=True, check=True).stdout
        try:
            with open(path, "rb") as fh:
                count = check(base.decode("utf-8"), fh.read().decode("utf-8"))
            print(f"{path}: 一致，新增样式行 {count}")
        except (RestyleError, UnicodeDecodeError) as exc:
            failed = 1
            print(f"{path}: 不一致：{exc}")
    return failed


if __name__ == "__main__":
    sys.exit(main())
