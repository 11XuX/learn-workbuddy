"""Usage: python3 scripts/check_mermaid_restyle.py --base <ref> <README...>
Minus added whitelisted style lines, the file must equal the baseline byte for byte (line endings
too); inside top-level mermaid blocks only leading indent may change."""
from __future__ import annotations
import argparse, re, subprocess, sys  # noqa: E401

VOCAB = {"input": ("输入", "Input"), "loop": ("主循环", "Agent Loop"),
         "dispatch": ("分发与校验", "Dispatch and Validation"), "tools": ("工具执行", "Tool Execution"),
         "memory": ("记忆与状态", "Memory and State"), "output": ("输出", "Output")}  # id -> (zh, en)
LINE = re.compile(r"([^\r\n]*)(\r\n|\r|\n|\Z)")
NAME, PROP = r"[A-Za-z_][A-Za-z0-9_]*", r"[a-z]+(?:-[a-z]+)*:[#A-Za-z0-9.]+"
CLASSDEF = re.compile(rf"classDef {NAME} {PROP}(?:,{PROP})*")
CLASS = re.compile(rf"class ({NAME}(?:,{NAME})*) {NAME}")
SUB_ID = re.compile(rf'subgraph ({NAME})\["([^"]+)"\]')
NODE = re.compile(r"[ \t]*(\w+)[\[({]")  # class ids: only line-start ID[ / ID( / ID{
BOUND = re.compile(r"(?i)[\s;]*(end|subgraph)\b")  # any end/subgraph variant is a baseline boundary
FENCE = re.compile(r"\s*(`{3,}|~{3,})(.*)")  # any fence; only a top-level "```mermaid" is a diagram


class RestyleError(ValueError): ...


def blocks(lines):  # fences nest per CommonMark; a block with an odd quote count is frozen (bad)
    blk, ids, toks, bad, cur, fence = [], [], [], set(), None, None
    for body, _ in lines:
        m = FENCE.fullmatch(body)
        if m and fence and m[1][0] == fence[0] and len(m[1]) >= len(fence) and not m[2].strip():
            fence = cur = None
        elif m and not fence:  # only a top-level "```mermaid" opens a diagram
            fence, cur, _ = m[1], len(ids) if body == "```mermaid" else None, (ids.append(set()), toks.append(set()))
        elif cur is not None:
            _ = blk.append(cur), (body.count('"') % 2 or body.count("'") % 2) and bad.add(cur)  # multi-line label
            ids[cur] |= set(NODE.match(body).groups() if NODE.match(body) else ())  # one per line
            toks[cur] |= set(re.findall(r"\w+", re.sub(r'"[^"]*"', " ", body)))
            continue
        blk.append(None)
    return blk, ids, toks, bad


def style_kind(text: str, ids: set[str], toks: set[str]) -> str:
    if text in ("", "end") or CLASSDEF.fullmatch(text):
        return text or "blank"
    m = CLASS.fullmatch(text)
    if m and set(m.group(1).split(",")) <= ids:
        return "class"
    clash = set(re.findall(r"\w+", text[9:].replace('"', " "))) & toks
    m = SUB_ID.fullmatch(text)  # subgraph title/id must not reuse any word of the block
    if not clash and (text[:9] == "subgraph " and text[9:] in sum(VOCAB.values(), ())
                      or m and m.group(2) in VOCAB.get(m.group(1), ())):
        return "subgraph"
    raise RestyleError(f"not an allowed style line: {text!r}")


def check(base_text: str, new_text: str) -> int:  # count of added style lines, else RestyleError
    base, new = ([m.groups() for m in LINE.finditer(x) if m[0]] for x in (base_text, new_text))  # (body, eol)
    if {e for _, e in base if e} != {e for _, e in new if e}:
        raise RestyleError("line-ending style changed")
    blk, ids, toks, bad = blocks(base)
    i = added = depth = nest = 0  # depth: open added subgraph; nest: baseline
    for n, (body, eol) in enumerate(new):
        ctx, old = blk[i - 1] if i else None, base[i] if i < len(base) else ("", None)
        text, t = body.lstrip(" \t"), old[0].lstrip(" \t")
        bt = (m := BOUND.match(t)) and m[1].lower()  # any end/subgraph variant in the baseline
        if eol == old[1] and (body == old[0] or blk[i] is not None and text == t):
            if depth and (blk[i] is None or bt):
                raise RestyleError(f"line {n + 1}: added subgraph not closed")
            nest += blk[i] is not None and (bt == "subgraph") - (bt == "end")
            i += 1
            continue
        if ctx is None or ctx in bad or eol != new[n - 1][1]:
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
            print(f"{path}: 一致，新增样式行 {check(base.decode(), open(path, 'rb').read().decode())}")
        except (RestyleError, ValueError, OSError, subprocess.SubprocessError) as exc:
            failed, _ = 1, print(f"{path}: 不一致：{exc}")
    return failed


if __name__ == "__main__":
    sys.exit(main())
