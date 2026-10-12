"""Usage: python3 scripts/check_mermaid_restyle.py --base <ref> <README...>
Minus added whitelisted style lines, the file must equal the baseline byte for
byte (line endings too); inside mermaid blocks only leading indent may change."""
from __future__ import annotations

import argparse, re, subprocess, sys  # noqa: E401

# Single source of the subgraph vocabulary: id -> (Chinese title, English title).
VOCAB = {"input": ("输入", "Input"), "loop": ("主循环", "Agent Loop"),
         "dispatch": ("分发与校验", "Dispatch and Validation"), "tools": ("工具执行", "Tool Execution"),
         "memory": ("记忆与状态", "Memory and State"), "output": ("输出", "Output")}
TITLES = {t for pair in VOCAB.values() for t in pair}
LINE = re.compile(r"([^\r\n]*)(\r\n|\r|\n|\Z)")
NAME = r"[A-Za-z_][A-Za-z0-9_]*"
PROP = r"[a-z]+(?:-[a-z]+)*:[#A-Za-z0-9.]+"
CLASSDEF = re.compile(rf"classDef {NAME} {PROP}(?:,{PROP})*")
CLASS = re.compile(rf"class ({NAME}(?:,{NAME})*) {NAME}")
SUB_ID = re.compile(rf'subgraph ({NAME})\["([^"]+)"\]')
NODE = re.compile(r"[ \t]*(\w+)[\[({]")  # class ids: only line-start ID[ / ID( / ID{


class RestyleError(ValueError): ...


def split_lines(text: str) -> list[tuple[str, str]]:  # (body, eol); eol '' on an unterminated last line
    return [m.groups() for m in LINE.finditer(text) if m.group(0)]


def blocks(lines):
    blk, ids, toks, cur = [], [], [], None
    for body, _ in lines:
        cur = None if cur is not None and body.strip().startswith("```") else cur
        blk.append(cur)
        if cur is None and body.strip() == "```mermaid":
            cur, _ = len(ids), (ids.append(set()), toks.append(set()))
        elif cur is not None:
            ids[cur] |= set(NODE.match(body).groups() if NODE.match(body) else ())  # one per line
            toks[cur] |= set(re.findall(r"\w+", re.sub(r'"[^"]*"', " ", body)))
    return blk, ids, toks


def style_kind(text: str, ids: set[str], toks: set[str]) -> str:
    if text in ("", "end") or CLASSDEF.fullmatch(text):
        return text or "blank"
    m = CLASS.fullmatch(text)
    if m and set(m.group(1).split(",")) <= ids:
        return "class"
    clash = set(re.findall(r"\w+", text[9:].replace('"', " "))) & toks
    m = SUB_ID.fullmatch(text)  # subgraph title/id must not reuse any word of the block
    if not clash and (text[:9] == "subgraph " and text[9:] in TITLES
                      or m and m.group(2) in VOCAB.get(m.group(1), ())):
        return "subgraph"
    raise RestyleError(f"not an allowed style line: {text!r}")


def check(base_text: str, new_text: str) -> int:  # count of added style lines, else RestyleError
    base, new = split_lines(base_text), split_lines(new_text)
    if {e for _, e in base if e} != {e for _, e in new if e}:
        raise RestyleError("line-ending style changed")
    blk, ids, toks = blocks(base)
    i = added = depth = nest = 0  # depth: open added subgraph; nest: baseline
    for n, (body, eol) in enumerate(new):
        ctx = blk[i - 1] if i else None
        old = base[i] if i < len(base) else ("", None)
        text, t = body.lstrip(" \t"), old[0].lstrip(" \t")
        if eol == old[1] and (body == old[0] or blk[i] is not None and text == t):
            if depth and (blk[i] is None or t == "end" or t[:9] == "subgraph "):
                raise RestyleError(f"line {n + 1}: added subgraph not closed")
            nest += blk[i] is not None and (t[:9] == "subgraph ") - (t == "end")
            i += 1
            continue
        if ctx is None or eol != new[n - 1][1]:
            raise RestyleError(f"line {n + 1}: changed outside mermaid, or EOL")
        kind = style_kind(text, ids[ctx], toks[ctx])
        same = {b.lstrip(" \t") for (b, _), k in zip(base, blk) if k == ctx}
        depth += {"subgraph": 1, "end": -1}.get(kind, 0)
        if depth not in (0, 1) or nest and kind == "subgraph" or kind not in (
                "blank", "end") and text in same:  # added subgraph: top level, one at a time
            raise RestyleError(f"line {n + 1}: bad end/subgraph nesting or duplicated line")
        added += 1
    if i != len(base) or depth:
        raise RestyleError(f"baseline line {i + 1} missing or changed")
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mermaid restyle byte check")
    parser.add_argument("--base", required=True), parser.add_argument("paths", nargs="+")
    args, failed = parser.parse_args(argv), 0
    for path in args.paths:
        try:
            base = subprocess.run(["git", "show", f"{args.base}:./{path}"], capture_output=True, check=True).stdout
            count = check(base.decode("utf-8"), open(path, "rb").read().decode("utf-8"))
            print(f"{path}: 一致，新增样式行 {count}")
        except (RestyleError, ValueError, OSError, subprocess.SubprocessError) as exc:
            failed, _ = 1, print(f"{path}: 不一致：{exc}")
    return failed


if __name__ == "__main__":
    sys.exit(main())
