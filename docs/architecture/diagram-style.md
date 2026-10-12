# 章节架构图样式规范（diagram-style）

本规范约束各章 README `## 代码架构图` 下的 mermaid 源块，只管**呈现**，不改内容。
配色与分层词表由本项目自拟；后续章节卡原样引用本文件，规范改动单独开卡。

## 1. 内容不变原则

- 节点 id、节点文字、边（起点、终点、边文字、线型）与改前完全一致。
- 原有行只允许改缩进；新增行只允许：`classDef`、`class`、`subgraph`/`end`，
  以及 subgraph 内只写节点 id 的成员行（如 `B`，不带文字）。
- 用 `python3 scripts/check_mermaid_restyle.py --base <git ref>` 机械证明以上两点，
  输出贴进 PR 描述，并写明基线 SHA。

## 2. 方向

- 保留原块的方向（目前各章均为 `flowchart LR`），不改成 `TD`。
- subgraph 内不写 `direction`。

## 3. subgraph 分层词表

subgraph 的 id 与标题只能取自下表（中文 README 用中文标题，英文 README 用英文标题）：

| id | 中文标题 | English title |
| --- | --- | --- |
| `input` | 输入 | Input |
| `loop` | 主循环 | Agent Loop |
| `dispatch` | 分发与校验 | Dispatch and Validation |
| `tools` | 工具执行 | Tool Execution |
| `memory` | 记忆与状态 | Memory and State |
| `output` | 输出 | Output |

只有两个及以上节点同属一层时才建 subgraph；单节点用 `class` 着色即可。
若加 subgraph 后渲染出现边文字重叠或布局明显变差，就不加，只用 `class` 着色（如 s01）。

## 4. classDef 调色板

浅底 + 深色描边 + 显式深色文字，GitHub light / dark 主题下都可读：

```text
classDef io fill:#e0f2fe,stroke:#0369a1,color:#0c2a3d
classDef core fill:#ede9fe,stroke:#6d28d9,color:#2e1065
classDef tool fill:#dcfce7,stroke:#15803d,color:#052e16
classDef data fill:#fef9c3,stroke:#a16207,color:#422006
classDef error fill:#fee2e2,stroke:#b91c1c,color:#450a0a
```

- `io`：外部输入与最终输出；`core`：主流程；`tool`：工具执行；`data`：配置/数据结构；
  `error`：错误结果。只写本块用到的 `classDef`。
- `classDef` 与 `class` 行放在块末尾，subgraph 放在边之后。

## 5. 禁止

- `click`、HTML 标签、图标（`fa:` 等）、`:::` 简写、`%%{init}%%` 主题覆盖、`style` 单点样式。
- 外部 SVG / mermaid 片段的复制。

## 6. 本地渲染核对（可选）

可用本机已有的 mermaid-cli 渲染 default 与 dark 两套主题核对可读性；
渲染工具不加入项目依赖，渲染产物不提交。
