"""Prove a mermaid restyle only changes presentation; rules: docs/architecture/diagram-style.md.

Conservative whitelist parser: each line is split on `;` outside quotes, every statement must
fullmatch one known form, anything else is rejected.
"""
import argparse
import re
import subprocess
import sys
from collections import Counter

DEFAULT_FILES = [f"{d}/README{s}.md" for d in ("s01_agent_loop", "s02_tool_dispatch") for s in ("", ".en")]
# subgraph id -> allowed titles (zh, en); keep in sync with docs/architecture/diagram-style.md
VOCAB = {"input": {"输入", "Input"}, "loop": {"主循环", "Agent Loop"},
         "dispatch": {"分发与校验", "Dispatch and Validation"}, "tools": {"工具执行", "Tool Execution"},
         "memory": {"记忆与状态", "Memory and State"}, "output": {"输出", "Output"}}
KEYWORDS = {"end", "subgraph", "class", "classdef", "flowchart", "graph", "style", "click", "direction"}
ID = r"[A-Za-z_][A-Za-z0-9_]*"
TXT = r'"([^"]*)"'
NODE = rf"({ID})(?:\[{TXT}\])?"
ARROWS = {"-->": "solid", "---": "open", "-.->": "dotted"}
FORMS = {
    "head": re.compile(r"flowchart (LR|RL|TD|TB|BT)"),
    "edge": re.compile(rf"{NODE}\s*(?:(-->|---|-\.->)\s*(?:\|{TXT}\|)?|-\.\s*{TXT}\s*\.->)\s*{NODE}"),
    "node": re.compile(rf"({ID})\[{TXT}\]"),
    "classdef": re.compile(rf"classDef ({ID}) ((?:fill|stroke|color):#[0-9a-fA-F]{{6}}"
                           rf"(?:,(?:fill|stroke|color|stroke-width):(?:#[0-9a-fA-F]{{6}}|\d+px))*)"),
    "class": re.compile(rf"class ({ID}(?:,{ID})*) ({ID})"),
    "subgraph": re.compile(rf"subgraph ({ID})\[{TXT}\]"),
    "end": re.compile(r"end"),
    "member": re.compile(rf"({ID})"),
}
BAD_TEXT = re.compile(r"<[A-Za-z/!]|\bfa[bsr]?:|[\[\]|]")


class RestyleError(ValueError):
    """A block is not a pure presentation change."""


def _check(ok: bool, msg: str) -> None:
    if not ok:
        raise RestyleError(msg)


def extract_blocks(text: str) -> list[list[str]]:
    blocks, cur = [], None
    for line in text.splitlines():
        if cur is None:
            cur = [] if line.strip() == "```mermaid" else None
        elif line.strip() == "```":
            blocks, cur = blocks + [cur], None
        else:
            cur.append(line)
    _check(cur is None, "未闭合的 mermaid 代码块")
    return blocks


def statements(lines: list[str]) -> list[str]:
    """Split on `;` outside quotes; drop blank statements and `%%` comment lines."""
    out = []
    for line in lines:
        if line.strip().startswith("%%"):
            _check(not line.strip().startswith("%%{"), f"禁止的主题覆盖: {line.strip()}")
            continue
        parts, cur, quoted = [], "", False
        for ch in line:
            quoted ^= ch == '"'
            parts, cur = (parts + [cur], "") if ch == ";" and not quoted else (parts, cur + ch)
        _check(not quoted, f"引号未闭合: {line.strip()}")
        out += [p.strip() for p in parts + [cur] if p.strip()]
    return out


def classify(st: str) -> tuple[str, tuple]:
    for kind, rx in FORMS.items():
        if m := rx.fullmatch(st):
            return kind, m.groups()
    raise RestyleError(f"无法识别的语法: {st}")


def parse(lines: list[str]) -> dict:
    body = statements(lines)
    _check(bool(body) and classify(body[0])[0] == "head", "首行必须是 flowchart <方向>")
    texts, edges, groups, class_defs, classed, members, open_group = {}, [], {}, set(), [], [], None
    for st in body[1:]:
        kind, g = classify(st)
        _check(kind != "head", f"重复的 flowchart 头: {st}")
        _check(not any(BAD_TEXT.search(t) for t in g[1:] if t and kind != "classdef"), f"禁止的文字内容: {st}")
        ids = [g[0], g[5]] if kind == "edge" else [g[0]] if kind in ("node", "member", "subgraph") else []
        _check(all(i.lower() not in KEYWORDS for i in ids), f"id 不能是关键字: {st}")
        if kind == "edge":
            src, stext, arrow, label, dlabel, dst, dtext = g
            edge = (src, dst, label if arrow else dlabel, ARROWS[arrow] if arrow else "dotted")
            _check(edge not in edges, f"重复的边: {st}")
            edges.append(edge)
            pairs = ((src, stext), (dst, dtext))
        elif kind == "node":
            pairs = ((g[0], g[1]),)
        elif kind == "subgraph":
            _check(open_group is None, f"不允许嵌套 subgraph: {st}")
            _check(g[0] not in groups, f"重复的 subgraph: {g[0]}")
            _check(g[1] in VOCAB.get(g[0], ()), f"subgraph 不在规范词表内: {st}")
            groups[g[0]], open_group = g[1], g[0]
            continue
        elif kind == "end":
            _check(open_group is not None, "多余的 end")
            _check(members and members[-1][0] == open_group, f"空 subgraph: {open_group}")
            open_group = None
            continue
        elif kind == "classdef":
            _check(g[0] not in class_defs, f"重复的 classDef: {g[0]}")
            class_defs.add(g[0])
            continue
        elif kind == "class":
            classed += [(i, g[1]) for i in g[0].split(",")]
            continue
        else:  # member
            _check(open_group is not None, f"成员行只能出现在 subgraph 内: {st}")
            members.append((open_group, g[0]))
            continue
        for nid, ntext in pairs:
            _check(ntext is None or texts.get(nid) is None, f"节点重复定义文字: {nid}")
            texts[nid] = ntext if ntext is not None else texts.get(nid)
    _check(open_group is None, "subgraph 未闭合")
    _check(None not in texts.values(), "存在没有文字的节点")
    _check(not set(groups) & set(texts), f"subgraph id 与节点 id 重名: {sorted(set(groups) & set(texts))}")
    names = [m for _, m in members]
    _check(set(names) <= set(texts) and len(names) == len(set(names)), f"subgraph 成员未知或重复: {names}")
    targets = [i for i, _ in classed]
    _check(len(targets) == len(set(targets)), f"同一节点重复指定 class: {targets}")
    _check(all(c in class_defs and i in set(texts) | set(groups) for i, c in classed),
           f"class 引用了未定义的类或节点: {classed}")
    return {"direction": body[0], "nodes": set(texts.items()), "edges": Counter(edges),
            "subgraphs": list(groups.items()), "lines": Counter(body)}


def compare(old_lines: list[str], new_lines: list[str]) -> list[tuple[str, str]]:
    """Raise RestyleError on any semantic change; return the newly added subgraphs."""
    old, new = parse(old_lines), parse(new_lines)
    for key, label in (("direction", "方向"), ("nodes", "节点 (id, 文字)"), ("edges", "边")):
        _check(old[key] == new[key], f"{label}不一致")
    missing = old["lines"] - new["lines"]
    _check(not missing, f"原有语句被修改或删除: {sorted(missing)}")
    return [sg for sg in new["subgraphs"] if sg not in old["subgraphs"]]


def check_file(base: str, path: str) -> list[str]:
    old = subprocess.run(["git", "show", f"{base}:{path}"], check=True,
                         capture_output=True, text=True).stdout
    with open(path, encoding="utf-8") as fh:
        old_blocks, new_blocks = extract_blocks(old), extract_blocks(fh.read())
    _check(len(old_blocks) == len(new_blocks), "mermaid 块数量不一致")
    return [f"块 {i}: 一致；新增 subgraph: {compare(o, n) or '无'}"
            for i, (o, n) in enumerate(zip(old_blocks, new_blocks), 1)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("files", nargs="*", default=DEFAULT_FILES)
    args, failed = ap.parse_args(argv), False
    for path in args.files:
        try:
            print("\n".join(f"{path}: {line}" for line in check_file(args.base, path)))
        except (RestyleError, subprocess.CalledProcessError) as exc:
            failed = print(f"{path}: 不一致 - {exc}") or True
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
