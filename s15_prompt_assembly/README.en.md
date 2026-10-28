# s15: Prompt Assembly - Select First, Assemble at Runtime

[中文](README.md) · [English](README.en.md)
> *A prompt is assembled from governed blocks; it is not one unstructured string.*
>
> **Harness layer: context selection, budget, authority, and system prompt assembly.**

This lesson gives memory selection an explicit contract for scope, confidence, authority, conflicts, deduplication, and token budget.

![Chapter diagram 1](./images/prompt-assembly-en.svg)

## What This Lesson Adds

```text
query → candidate → score → stable rank → RecallResult
```

```text
RecallResult
  → scope gate
  → confidence gate
  → authority precedence
  → exact dedupe
  → explicit conflict resolution
  → stable top-k / budget packing
  → <recalled_memory>
  → total prompt segment planner
```

## Code Architecture Diagram

```mermaid
flowchart LR
    A["S12 RecallHit"] --> B["S15 Candidate Adapter"]
    B --> C["Scope + Confidence"]
    C --> D["Authority Precedence"]
    D --> E["Dedupe + Conflict"]
    E --> F["Stable top-k"]
    F --> G["Char / Token Pack"]
    G --> H["Recalled Memory Segment"]
    H --> I["Total Prompt Planner"]
    I --> J["System Prompt + Two Decision Logs"]
```

## s12 and s15 Responsibilities

| Chapter | Owns | Does not own |
|---|---|---|
| S12 Remote Memory | Query normalization, candidate generation, score breakdown, stable ranking, scope, and provenance | The final decision about which hits enter the prompt |
| S15 Prompt Assembly | Scope/confidence gates, authority, deduplication, conflicts, top-k, budgets, and prompt assembly | Retrieval scoring or long-term memory writes |

s12 produces scoped recall candidates; s15 decides which candidates are authoritative, deduplicated, and affordable to place in the prompt.

## Memory Selection Contract

### `MemoryContextCandidate`

### `MemorySelectionPolicy`

```python
policy = MemorySelectionPolicy(
    min_score=0.35,
    top_k=5,
    max_chars=3_000,
    max_tokens=800,
)
```

```python
plan = select_memory_context(
    candidates,
    user_scope=current_scope,
    policy=policy,
    token_counter=target_model_tokenizer,
)
```

### `MemoryContextPlan`

## Three Preference Authority Levels, Not Three Score Weights

| Authority | Meaning | Example |
|---|---|---|
| `current_turn` | Explicit requirement for the current task | "Use Go for this implementation" |
| `workspace_override` | Local convention of the current project | "This repository uses TypeScript for automation" |
| `user_default` | Cross-project personal default | "I usually prefer Python" |

```text
current_turn > workspace_override > user_default
```

```text
authority desc → score desc → source_rank asc → captured_at desc → memory_id asc
```

### Authority Is Not Harness Required

`current_turn` is the highest level among preference candidates, but it is still not a system safety rule. Base instructions, tool boundaries, and Working mode continue to be carried by required `PromptSegment`s:

- authority only decides, among candidates, who wins and who uses the Memory budget first;
- required segments reserve the total Prompt budget first, and no preference can override or squeeze them out;
- when the required segments themselves do not fit, `PromptBudgetError` is raised; safety rules are never downgraded to optional preferences.

The current-turn user message is still passed to the model through conversation messages; the `current_turn` candidate is its structured arbitration projection and audit evidence, and must not replace the original user message.

## Why Selection Order Matters

### 1. Scope Gate

### 2. Confidence Gate

### 3. Authority Precedence

### 4. Exact Deduplication

### 5. Explicit Conflict Resolution

```text
score desc → source_rank asc → captured_at desc → memory_id asc
```

### 6. Top-k and Budget

## Two Budget Layers, Not One Vague Length Limit

```text
Memory candidates
  └─ MemorySelectionPolicy
       └─ <recalled_memory> segment
            └─ plan_prompt(total prompt budget)
                 └─ final system prompt
```

## System Prompt Sections

| # | Segment | Source | Condition | Total-budget policy |
|---|---|---|---|---|
| 1 | Base instructions | Harness rules | Always | required |
| 2 | Identity | SOUL / IDENTITY / USER | File exists or teaching default | Optional, high value |
| 3 | Recalled memory | S12 -> S15 selection plan | The query selected hits | Optional, medium value |
| 4 | Project context | Workspace structure | Always built | Optional, high value |
| 5 | Tool descriptions | Tool registry | Tools exist | required |
| 6 | Expert instructions | Active expert | When active | Optional, high value |
| 7 | Skill instructions | Loaded `SKILL.md` | When loaded | Optional, high value |
| 8 | Connector status | MCP connectors | When connectors exist | Optional, lower value |
| 9 | Regional conventions | Current region | Region-specific | Optional, low value |
| 10 | Working mode | craft / plan / ask | Always | required |

### Display Order and Budget Values

```text
Build fragments and record provenance
  → reserve required fragments first
  → fail closed when required fragments exceed the budget
  → try to add optional fragments by budget_priority
  → render selected fragments by priority
  → PromptPlan + SegmentDecision
```

## Runtime Assembly

Runtime assembly orders governed blocks, records the selected evidence, and fails closed when required context cannot fit the budget.

| Event | Affected segment |
|---|---|
| New S12 recall candidates | Memory selection and memory segment |
| Query ends or user scope changes | Clear the memory segment to prevent leakage |
| Skill loads | Skill instructions |
| Expert switches | Expert instructions |
| Working mode changes | Working mode |
| Connector starts/stops | Connector status |
| Identity file changes | Identity |

## Demo Without an API Key

```bash
python s15_prompt_assembly/code.py
```

```text
memory
```

```text
memory clear
```

```bash
PROMPT_BUDGET_CHARS=2000 python s15_prompt_assembly/code.py
```

## Common Mistakes

Do not select by recency alone, mix scopes, spend the entire budget on optional context, or hide why a candidate was excluded.

## Interview Questions

1. What problems do retrieval, selection, packing, and prompt assembly each solve?
2. Why must scope and confidence come before deduplication, conflicts, and budget?
3. Why does a conflict need an explicit slot instead of being replaced by string deduplication?
4. After a high-scoring long candidate exceeds the budget, why may a small candidate backfill while a conflict loser may not?
5. Why do we need two audits, a memory decision log and a segment decision log?
6. How can a real tokenizer be integrated without breaking offline regression and the upper-level API?
7. How can exact deduplication be upgraded to semantic deduplication while keeping deterministic replay?
8. Why must authority come before score, and why must it still come after the scope and confidence gates?
9. What are the permission boundaries of `current_turn` and a required PromptSegment, respectively?

## Exercises

1. Inject the target model's tokenizer, compare how selection differs from the default estimator, and verify that the decision log of each run is reproducible.
2. Add a structured `fact_type + subject` upstream to produce a more reliable conflict key.
3. Add a "must keep" Memory type, consider how it differs from the Harness required safety rules, and whether it should fail closed when the budget is insufficient.
4. Aggregate decision reasons into offline eval metrics, such as scope rejection rate, low-confidence abstention rate, and budget utilization.
5. Add a signature or event binding to the authority source, and show that the hierarchy is produced by the trusted Harness rather than forged by the model.

## Next Lesson

- This lesson gives memory selection an explicit contract for scope, confidence, authority, conflicts, deduplication, and token budget.

The original Chinese README remains the primary-language reference. This English companion keeps the same code, diagrams, local paths, and chapter structure so readers can switch languages without losing runnable details.

Local references:

- [Reference](README.md)
- [Reference](README.en.md)
- [Reference](./images/prompt-assembly-en.svg)
