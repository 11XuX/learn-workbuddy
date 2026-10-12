"""Prove a mermaid restyle only changes presentation; rules: docs/architecture/diagram-style.md."""
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
ID = r"[A-Za-z_][A-Za-z0-9_]*"
NODE = rf'({ID})(?:\["([^"\]]*)"\])?'
ARROWS = {"-->": "solid", "---": "open", "-.->": "dotted"}
EDGE = re.compile(rf'^{NODE}\s*(?:(-->|---|-\.->)\s*(?:\|"([^"|]*)"\|)?'
                  rf'|-\.\s*"([^"]*)"\s*\.->)\s*{NODE}$')
FORBIDDEN = re.compile(r"click\b|<[A-Za-z/]|:::|%%\{|\bfa:|^style\b")
STYLE = re.compile(rf"^(classDef {ID} \S+|class {ID}(,{ID})* {ID}|subgraph ({ID})\[\"([^\"]+)\"\]|end|{ID})$")


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


def parse(lines: list[str]) -> dict:
    body = [ln.strip() for ln in lines if ln.strip()]
    _check(bool(body) and bool(re.fullmatch(r"flowchart (LR|RL|TD|TB|BT)", body[0])),
           "首行必须是 flowchart <方向>")
    texts, edges, subgraphs, class_defs, class_uses, members, depth = {}, [], [], set(), [], [], 0
    for ln in body[1:]:
        _check(not FORBIDDEN.search(ln), f"禁止的语法: {ln}")
        if m := EDGE.match(ln):  # groups: src, src text, arrow, |label|, dotted label, dst, dst text
            src, stext, arrow, label, dlabel, dst, dtext = m.groups()
            edge = (src, dst, label if arrow else dlabel, ARROWS[arrow] if arrow else "dotted")
            _check(edge not in edges, f"重复的边: {ln}")
            edges.append(edge)
            for nid, ntext in ((src, stext), (dst, dtext)):
                _check(ntext is None or texts.get(nid) is None, f"节点重复定义文字: {nid}")
                texts[nid] = ntext if ntext is not None else texts.get(nid)
            continue
        s = STYLE.match(ln)
        _check(bool(s), f"无法识别的语法: {ln}")
        if s.group(3):
            _check(s.group(4) in VOCAB.get(s.group(3), ()), f"subgraph 不在规范词表内: {ln}")
            subgraphs.append((s.group(3), s.group(4)))
            depth += 1
        elif ln == "end":
            depth -= 1
            _check(depth >= 0, "多余的 end")
        elif ln.startswith("classDef "):
            class_defs.add(ln.split()[1])
        elif ln.startswith("class "):
            class_uses.append((ln.split()[1].split(","), ln.split()[2]))
        else:
            _check(depth > 0, f"成员行只能出现在 subgraph 内: {ln}")
            members.append(ln)
    _check(not depth, "subgraph 未闭合")
    _check(set(members) <= set(texts) and len(members) == len(set(members)),
           f"subgraph 成员未知或重复: {members}")
    for ids, name in class_uses:
        _check(name in class_defs and set(ids) <= set(texts) | {sid for sid, _ in subgraphs}, f"class 引用了未定义的类或节点: {ids} {name}")
    _check(None not in texts.values(), "存在没有文字的节点")
    return {"direction": body[0], "nodes": set(texts.items()), "edges": Counter(edges),
            "subgraphs": subgraphs, "lines": Counter(body)}


def compare(old_lines: list[str], new_lines: list[str]) -> list[tuple[str, str]]:
    """Raise RestyleError on any semantic change; return the newly added subgraphs."""
    old, new = parse(old_lines), parse(new_lines)
    for key, label in (("direction", "方向"), ("nodes", "节点 (id, 文字)"), ("edges", "边")):
        _check(old[key] == new[key], f"{label}不一致")
    missing = old["lines"] - new["lines"]
    _check(not missing, f"原有行被修改或删除: {sorted(missing)}")
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
