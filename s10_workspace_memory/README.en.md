# s10: Workspace Memory - Turn Work Logs into Durable Project Facts

[中文](README.md) · [English](README.en.md)
> *Workspace memory records facts that belong to a project, not to a person or a single turn.*
>
> **Harness layer: scoped memory and durable project context.**

The lesson shows how to record, normalize, deduplicate, and recover project facts without treating every conversation sentence as memory.

![Chapter diagram 1](./images/workspace-memory-en.svg)

## Code Architecture Diagram

```mermaid
flowchart LR
    A["Tool / Agent outcome"] --> B["validate MemoryFact"]
    B --> C["daily/*.jsonl append"]
    C --> D["DistillPolicy gate"]
    D -->|"important or repeated"| E["group by memory_key"]
    D -->|"not stable"| C
    E --> H{"one strongest newer value?"}
    H -->|"yes"| I["new active revision"]
    H -->|"stale"| C
    H -->|"tie"| Q["conflicts.json review queue"]
    Q --> R["human resolve_conflict"]
    R --> T["append-only transaction journal"]
    T --> K["append-only adjudication audit"]
    T -->|"challenger"| I
    T -->|"incumbent"| J
    I --> J["curated.json history"]
    J --> F["active-only MEMORY.md"]
    F --> G["bounded prompt context"]
    C -. "evidence retained" .-> J
```

## Storage and Authority

Daily facts are evidence, curated history is the adjudicated record, and the rendered memory file contains only active context.

## How a Fact Enters Long-Term Memory

```text
project/
└── .learn_workbuddy/
    └── memory/
        ├── daily/
        │   ├── 2026-08-07.jsonl
        │   └── 2026-08-08.jsonl
        ├── curated.json
        ├── conflicts.json
        ├── conflict-adjudications.jsonl
        ├── conflict-resolution-transactions.jsonl
        └── MEMORY.md
```

## Record the Fact

### Normalize and Deduplicate

```python
memory.append_daily_log(
    "SQLite must run in WAL mode.",
    kind=FactKind.DECISION,
    importance=5,
    memory_key="storage.sqlite.journal-mode",
    source="agent",
    evidence={"file": "storage.py"},
)
```

### Resolve Same-Key Conflicts

```text
age reaches 30 days
AND type is one of decision / convention / pitfall
AND (importance >= 4 OR reappears after normalization >= 2 times)
```

### Append-Only Decisions

### Preserve Source Evidence

```python
case = memory.list_conflicts()[0]
choice = next(c for c in case.candidates if c.content == "Use Postgres.")

event = memory.resolve_conflict(
    case.conflict_id,
    choice.candidate_id,
    expected_revision=case.revision,
    actor="reviewer@example.com",
    rationale="Production requires PostgreSQL extensions.",
    event_id="review:storage-database:42",
)
```

### Atomic Updates, Locks, and Recovery

```text
daily fact log ──select──> curated.json ──render──> MEMORY.md
       │                    │
       └──── retained ──────┘  evidence_ids
```

### Prompt Injection Boundary

```text
prepared
  -> curated_applied
  -> conflict_closed
  -> audit_appended
  -> committed
```

## Main Code Paths

The main paths are append, distill, resolve, recover, and render; each path preserves provenance and workspace scope.

## Failure and Recovery Tests

```text
append_daily_log
  -> validate kind / importance / content / optional memory_key
  -> attach workspace_id + fact_id + UTC timestamp
  -> append one JSONL record + fsync

distill
  -> load facts older than cutoff
  -> reject unstable kinds
  -> preserve legacy content groups; group keyed facts by conflict domain
  -> apply importance/repetition gate
  -> reject stale challengers; persist tied candidates as a conflict case
  -> merge evidence or append a linked revision
  -> atomically replace curated.json / conflicts.json / MEMORY.md

human review
  -> list open conflict snapshot
  -> submit expected revision + selected candidate + source event
  -> reject changed evidence or reused event IDs
  -> append prepared intent with before/after snapshots
  -> apply curated state -> close conflict -> append audit
  -> append committed phase; retry each boundary idempotently

restart
  -> resolve the same project scope
  -> validate workspace_id and schema
  -> replay every non-committed resolution transaction
  -> reload daily facts + curated state + conflict queue + adjudication audit
  -> rebuild bounded prompt context
```

## Where It Sits in the Loop

```bash
python3 s10_workspace_memory/code.py --demo
```

```bash
python3 -m pytest -q tests/test_workspace_memory.py
```

```bash
python3 s10_workspace_memory/code.py
```

```text
/resolve <conflict_id> <revision> <candidate_id> <event_id> <rationale>
```

## Run

![Chapter diagram 2](./images/three-layer-memory-en.svg)

## Source Confirmation Bonus

### Problem

`MemoryFact.source` is already persisted, but the promotion gate ignores it. The model also has `bash` and can append a line with `source="user_confirmed"` to the daily log, so any bonus driven by that field can be forged by one shell command.

### Solution

The confirmation credential lives in harness process memory. `WorkspaceMemory.confirm_fact()` persists the fact as usual (`source="user_confirmed"`) and records the whole `MemoryFact` in `_session_confirmed`. `DistillPolicy.confirmed_importance_bonus` (default 1) applies only when `_session_confirmed.get(fact.fact_id) == fact`.

### How It Works

```mermaid
flowchart LR
    U[/confirm/] --> C[confirm_fact]
    C --> L[(daily log)]
    C --> S[_session_confirmed]
    B[bash] --> L
    L --> D{distill: whole record in S?}
    S --> D
    D -- yes --> P[importance + bonus]
    D -- no --> N[normal gate]
```

- The logged `source` is only an audit label; a forged fact_id or a reused fact_id with edited content fails the comparison.
- The bonus applies only to first promotion (no active entry for the key); supersession, adjudication, and the journal are unchanged.
- Residual limits: confirmation does not survive a restart; with the default `minimum_age_days=30`, `/distill` right after `/confirm` promotes nothing (tests use `distill(as_of=...)`); a model with a shell can still edit other log fields such as importance, which belongs to the s04 permission policy.

### Try It

```text
s10 >> /confirm convention 3 Run ruff before commit
confirmed: [convention] Run ruff before commit (3/5, source=user_confirmed, confirmed=session)
s10 >> /today
[convention] Run ruff before commit (3/5, source=user_confirmed, confirmed=session)
```

### Architecture Mapping

| Write path | Persisted source | First-promotion bonus |
|---|---|---|
| CLI `/confirm` | `user_confirmed` | +1 in this process |
| `write_memory` tool | `model_tool` | none |
| old confirmation after restart | `user_confirmed` | none |

## Common Mistakes

Do not promote every sentence to memory, overwrite history during deduplication, or resolve a conflict without recording the decision and evidence.

## Exercises

Use these exercises to change one part of workspace-owned durable facts, provenance, and atomic recovery at a time and explain the resulting contract. Source confidence is already implemented as an in-process confirmation bonus (see Source Confirmation Bonus); confirmation does not survive a restart.

## Next Lesson

- The lesson shows how to record, normalize, deduplicate, and recover project facts without treating every conversation sentence as memory.

**Reference tables**

| File | Identity | Write method | Can serve as evidence |
|---|---|---|---|
| `daily/*.jsonl` | Raw fact log | One-record `O_APPEND` | Yes |
| `curated.json` | Machine truth for curated state | Temporary file + `os.replace` | Traceable to evidence IDs |
| `conflicts.json` | Pending and historical conflict snapshots | Temporary file + `os.replace` | Yes; keeps candidates and revisions |
| `conflict-adjudications.jsonl` | Human adjudication audit stream | One-event `O_APPEND` | Yes; actor, rationale, and selected evidence |
| `conflict-resolution-transactions.jsonl` | Multi-file recovery log | Append-only phase events | Yes; intent hash and commit phase |
| `MEMORY.md` | Derived human/prompt view | Rebuilt atomically from curated state | No; always rebuildable |

| Type | Example | Distilled by default? |
|---|---|---|
| `decision` | Choose SQLite WAL | Yes |
| `convention` | Paths must be relative to the workspace | Yes |
| `pitfall` | Never write tokens into memory | Yes |
| `outcome` | This test passed | No; keep it in the recent log |

The original Chinese README remains the primary-language reference. This English companion keeps the same code, diagrams, local paths, and chapter structure so readers can switch languages without losing runnable details.

Local references:

- [Reference](README.md)
- [Reference](README.en.md)
- [Reference](./images/workspace-memory-en.svg)
- [Reference](./images/three-layer-memory-en.svg)
