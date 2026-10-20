# s16: Skills System - List First, Expand on Demand

[中文](README.md) · [English](README.en.md)
> *Skills are indexed as short instructions and loaded only when a task needs them.*
>
> **Harness layer: extensibility, lazy loading, and skill permission boundaries.**

A skill combines frontmatter, instructions, optional scripts, and explicit permissions. Indexing stays small; full content is loaded on demand.

![Chapter diagram 1](./images/skills-system-en.svg)

## Code Architecture Diagram

```mermaid
flowchart LR
    A["Skill Directory"] --> B["Skill Search"]
    B --> C["Skill Loader"]
    C --> P["Permission Manifest"]
    C --> D["Prompt Merge"]
    P --> G{"Policy Gate"}
    H["Harness Permission Ceiling"] --> G
    D --> E["Agent Tool Call"] --> G
    G -->|Allow| X["Tool Dispatch"]
    G -->|Deny| Y["Permission Error"]
    C -. "state" .-> S["runtime state"]
    S -. "recover" .-> C
```

## Prerequisites

The examples use local SKILL.md files and a small permission manifest; no marketplace or network service is required.

## WorkBuddy-Style Mechanisms in This Chapter

The lesson keeps the skill index small, loads full instructions on demand, and applies an explicit permission ceiling.

## Common Mistakes

Do not load every skill into every prompt, execute a skill before its permissions are checked, or confuse discovery with full loading.

## The Problem

The harness needs extensibility without allowing a growing skill catalog to consume context or bypass execution policy.

## The Solution

Two-level storage + on-demand loading (**production shape** below; this teaching REPL currently does not read these directories — see the teaching-boundary note under How It Works):

```
Skill storage:

  user-level: ~/.workbuddy/skills/          (personal, shared across projects)
    ├── git-commit/
    │   └── SKILL.md
    ├── code-review/
    │   └── SKILL.md
    └── deploy-check/
        └── SKILL.md

  project-level: {workspace}/.workbuddy/skills/ (project-specific, shared with the team)
    ├── api-design/
    │   └── SKILL.md
    └── test-conventions/
        └── SKILL.md
```

```
At startup:
  ┌─────────────────────────────────────────────────┐
  │ 1. scan all SKILL.md files in both directories                    │
  │ 2. parse only frontmatter (title, summary, read_when)│
  │ 3. build the skill index (do not load full content)                   │
  │ 4. inject the index into the system prompt (use only a few hundred tokens)             │
  └─────────────────────────────────────────────────┘

When the user enters a request:
  ┌─────────────────────────────────────────────────┐
  │ 1. User input: "Help me commit code"                        │
  │ 2. match trigger words: "commit" → git-commit Skill            │
  │ 3. load the complete git-commit/SKILL.md              │
  │ 4. inject into the system prompt (reassemble, s15)                    │
   │ 5. agent receives the complete guide to committing code                   │
  └─────────────────────────────────────────────────┘
```

## How It Works

> **Teaching boundary (current):** A production Skills system scans user-level `~/.workbuddy/skills` and project-level `{workspace}/.workbuddy/skills` for `*/SKILL.md`. **This teaching chapter currently** builds the index only from the in-memory `SEED_SKILLS` dict; `create_skill` / `approve` only mutate the in-memory skill index and `pending_skills`, which disappear when the process exits. Directory diagrams and `scanSkillDir` sketches below are the **production shape** — this teaching REPL **currently** does not read those directories (that does not permanently rule out a future on-disk loader).

### Frontmatter Fields

```markdown
---
title: git-commit
summary: Standard Git commit workflow
read_when:
  - commit code
  - commit
  - git push
  - preserve changes
agent_created: false
permissions:
  tools: [bash]
  network: false
  paths:
    read: ["**"]
    write: []
---

# Git Commit Skill

## Steps
1. Run `git status` to inspect changes
2. Run `git diff` to inspect changes
3. Stage relevant files `git add`
4. generate a conventional commit message:
   - format: `type(scope): description`
   - type: feat/fix/docs/refactor/test/chore
5. commit: `git commit -m`

## Notes
- Do not commit sensitive information
- Use Chinese for the commit message
```

```text
Harness Base permissions ∩ currently approved Skill manifest
```

### Frontmatter Parsing

```python
import yaml

def parse_skill_md(filepath: Path) -> dict | None:
    """parse SKILL.md and extract its frontmatter and body."""
    content = filepath.read_text()

    # Extract YAML frontmatter (--- wrapped)
    if not content.startswith("---"):
        return None

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter = yaml.safe_load(parts[1])
    body = parts[2].strip()

    return {
        "path": str(filepath),
        "dir": str(filepath.parent),
        "title": frontmatter.get("title", filepath.parent.name),
        "summary": frontmatter.get("summary", ""),
        "read_when": frontmatter.get("read_when", []),
        "agent_created": frontmatter.get("agent_created", False),
        "content": body,
    }
```

### Index Building

```python
def build_skill_index() -> list[dict]:
    """Build the index from SEED_SKILLS (teaching version).

    Parse frontmatter only (title, summary, read_when); body stays in memory and is marked loaded on demand.
    # Production: scan Path.home()/".workbuddy"/"skills" and WORKDIR/".workbuddy"/"skills" instead.
    """
    index = []
    for name, content in SEED_SKILLS.items():
        fm, body = parse_frontmatter(content)
        index.append({
            "title": fm.get("title", name),
            "summary": fm.get("summary", ""),
            "read_when": fm.get("read_when", []),
            "path": f"(memory)/{name}/SKILL.md",
            "content": body,
            "loaded": False,
        })
    return index
```

### Trigger Matching

```python
def match_skill(user_input: str) -> str | None:
    """Check whether user input matches a skill's trigger words.

    simple keyword matching.Real WorkBuddy may use semantic matching.
    """
    input_lower = user_input.lower()

    for skill in skill_index:
        for trigger in skill["read_when"]:
            if trigger.lower() in input_lower:
                return skill["title"]

    return None
```

### On-Demand Loading

```python
def load_skill(title: str) -> str:
    """Load a skill's full content into context (teaching: body already in the index).

    1. Find the skill in the index
    2. Mark as loaded (do not re-read from Path)
    3. Return a status string with the body
    """
    for skill in skill_index:
        if skill["title"] == title:
            if skill["loaded"]:
                return f"Skill '{title}' is already in context."
            skill["loaded"] = True
            return f"Skill '{title}' loaded. Content:\n{skill['content']}"
    return f"Skill '{title}' not found. Available: {[s['title'] for s in skill_index]}"
```

### The Skill Tool

```python
def skill_tool(skill: str) -> str:
    """Skill tool called by the model.

    The model calls this tool when it determines that a skill is needed during a conversation.
    """
    if load_skill(skill):
        return f"Skill '{skill}' loaded."
    return f"Skill not found '{skill}'.Available skills: {[s['title'] for s in skill_index]}"
```

### Skill Creation

After a task, the model can call the `SkillCreate` tool to propose saving the workflow as a new skill; the harness audits it first, then rejects it, queues `pending_skills`, or indexes it in memory (teaching version does not write to disk):

```python
def create_skill(title: str, summary: str, read_when: list[str],
                 content: str, permissions=None) -> str:
    """Create a new skill (teaching: memory index / pending_skills only; no disk write).

    A production harness typically writes ~/.workbuddy/skills/{title}/SKILL.md.
    """
    try:
        skill_md, requested = build_created_skill_md(
            title, summary, read_when, content, permissions
        )
    except Exception as exc:
        return f"Rejected skill creation: {exc}"

    if any(s["title"] == title for s in skill_index):
        return f"Skill '{title}' already exists."
    if title in pending_skills:
        return f"Skill '{title}' is already pending approval; not overwritten."

    # Write boundary: audit first, then reject / pending / index in memory (no mkdir / write_text)
    level, report = audit_skill(skill_md, requested_permissions=requested)
    if level == "P0":
        return f"Rejected skill '{title}': {report}"
    if level == "P1":
        pending_skills[title] = PendingSkill(
            title, skill_md, report, time.time(), requested
        )
        return f"Skill '{title}' is pending user approval: {report}"

    _index_created_skill(skill_md, requested)  # updates skill_index and SEED_SKILLS
    return f"Skill '{title}' created."
```

### Security Audit

```python
def audit_skill(
    skill_content: str,
    previous_permissions=None,
    *,
    requested_permissions=None,
) -> tuple[str, str]:
    """Check skill content for safety (teaching: pattern-match on skill_content).

    Returns: (risk_level, report)
    risk_level: P0 (forbidden) / P1 (approval required) / P2 (allowed)
    """
    # P0: hard-blocked content
    p0_patterns = ["rm -rf /", "sudo ", "eval(", "exec(", "os.system"]
    for pattern in p0_patterns:
        if pattern in skill_content:
            return ("P0", f"forbidden installation: contains dangerous pattern '{pattern}'")

    # P1: requires user confirmation
    p1_patterns = ["curl ", "wget ", "npm install", "pip install", "git clone"]
    for pattern in p1_patterns:
        if pattern in skill_content:
            return ("P1", f"approval required: contains network/install operations '{pattern}'")

    # P2: safe (permission merge / escalation lives in code.py; not expanded here)
    return ("P2", "Safe: no dangerous patterns")
```

**Try it**: no API key needed. Run the chapter REPL and type `/skill-gate-demo`. It calls the same `create_skill()` the model reaches through `TOOL_HANDLERS["SkillCreate"]`: a skill containing `curl` goes to pending approval and `match_skill` cannot trigger it; only after the demo plays the user's `approve` command does it match. One extra line each shows P2 created directly and P0 rejected without entering the pending queue. The demo's approve stands for the command a user types in the local terminal; the model's tool schema still has no approval field. Everything stays in memory and the skill index and pending queue are restored afterwards, so a following `skills` shows no `gate-demo-*`. A sample run is in the Chinese README (the demo prints Chinese status lines).

```bash
MODEL_ID=offline python3 s16_skills_system/code.py   # then type: /skill-gate-demo
```

## The Tool Schema Token Problem

### Two-Step Loading

```
80+ tool definitions × ~500 tokens/tools = ~40,000 tokens
  (consume context whether or not they are used)
```

### Teaching Implementation: CLI Bundle

```
Traditional approach: load everything at startup
  80 tool schemas → inject all into the system prompt → 40K tokens
  (consume context whether or not they are used)

WorkBuddy approach: load on demand (deferLoading)
  Step 1: ToolSearch (lightweight index)
    ├─ Input: keyword search
    ├─ Output: matching tool names + brief descriptions
    └─ do not load the full schema

  Step 2: DeferExecuteTool (expand on demand)
    ├─ Input: tool name + parameters
    ├─ load the full input_schema only at this point
    └─ validate parameters, then execute
```

### Key Clean-Room Pattern

```javascript
// Check the deferLoading flag when registering a tool
for (let tool of tools) {
    if (tool.deferLoading) {
        // index only the tool name + short description
        let indexed = this.indexDeferredTool(tool);
        deferredTools.push(indexed);
    } else {
        // load the full schema immediately
        fullTools.push(tool);
    }
}

// When the agent needs a deferred tool:
// 1. Agent calls ToolSearch (keyword search)
// 2. system returns matching tool names + descriptions
// 3. Agent calls DeferExecuteTool (tool name + parameters)
// 4. system loads the full schema, validates parameters, and executes
```

### Deferred Tools in WorkBuddy

### Three Skill Storage Levels

```
Deferred tools (loaded only when used):
  - ImageGen: text-to-image generation
  - connect_cloud_service: connect to cloud services
  - EnterPlanMode / ExitPlanMode: plan-mode control
  - TaskStop: stop background tasks
  - ListMcpResources / ReadMcpResource: MCP resource access
  - workbuddy_marketplace_skill: skill marketplace search/installation
  - mcp__ardot: design-tool MCP (20+ sub-tools)
  - mcp__weixinpay: WeChat Pay MCP
```

## Skill Directory Layout

(**Production shape**; this teaching REPL currently does not read these directories.)

```
┌─────────────────────────────────────────────┐
│            Skill storage hierarchy                     │
├─────────────────────────────────────────────┤
│                                              │
│  Built-in Skills (10)                           │
│  Location: unpacked runtime resources/resources/         │
│         builtin-skills/                      │
│  Characteristics: distributed with the application and cannot be modified                    │
│  Examples: skill-creator, expert-manager,        │
│        cloudstudio-deploy, westock-data      │
│                                              │
│  User-level Skills                                │
│  Location: ~/.workbuddy/skills/                  │
│  Characteristics: shared across projects and personally customized                    │
│  Examples: user-defined workflows                      │
│                                              │
│  Project-level Skills                                │
│  Location: {workspace}/.workbuddy/skills/        │
│  Characteristics: project-specific and shared with the team                      │
│  Examples: project-specific deployment flows                      │
│                                              │
└─────────────────────────────────────────────┘
```

### WorkBuddy Architecture Comparison

This comparison maps skill discovery, frontmatter catalogs, deferred loading, and auditing to the corresponding WorkBuddy-style harness boundary.

```
my-skill/
  ├── SKILL.md          # instruction injection (frontmatter + body)
  ├── scripts/          # executable scripts
  │   └── index.js
  ├── references/       # reference documents
  │   └── api-spec.md
  └── assets/           # resource files
      └── template.html
```

## WorkBuddy Architecture Comparison

This comparison maps skill discovery, frontmatter catalogs, deferred loading, and auditing to the corresponding WorkBuddy-style harness boundary.

### Frontmatter Fields

(**Production shape**; this teaching REPL currently indexes `SEED_SKILLS` and does not scan disk.)

```
~/.workbuddy/skills/              # user-level (personal, shared across projects)
  git-commit/SKILL.md
  code-review/SKILL.md

{workspace}/.workbuddy/skills/    # project-level (shared with the team)
  api-design/SKILL.md
```

### Loading Flow

```yaml
---
title: skill-name           # skill name (unique identifier)
summary: one-line description           # shown in the index
read_when:                  # trigger conditions (keyword list)
  - commit code
  - commit
agent_created: false         # whether created by the model
permissions:                 # minimum capabilities requested by the Skill
  tools: [read_file]
  network: false
  paths:
    read: ["docs/**"]
    write: []
---
```

### Security Audit

(The `scanSkillDir` calls below are the **production shape**; this teaching REPL currently does not read those directories — the index comes from `SEED_SKILLS`.)

```javascript
// skill loading in the agent bridge (simplified)

// 1. scan the index at startup
function buildSkillIndex() {
    const userSkills = scanSkillDir('~/.workbuddy/skills/');
    const projectSkills = scanSkillDir(`${workdir}/.workbuddy/skills/`);
    return [...projectSkills, ...userSkills]; // project-level first
}

// 2. inject the index into the system prompt (title + summary only)
function buildSkillIndexPrompt(skills) {
    return skills.map(s =>
        `- ${s.title}: ${s.summary}`
    ).join('\n');
}

// 3. trigger matching
function matchSkills(userInput, skills) {
    return skills.filter(s =>
        s.read_when.some(trigger =>
            userInput.toLowerCase().includes(trigger.toLowerCase())
        )
    );
}

// 4. load full content
function loadSkillContent(skill) {
    const full = parseSkillMd(skill.path);
    loadedSkills.push(full);
    reassembleSystemPrompt(); // s15
}

// 5. Skill tool
const SkillTool = {
    name: "Skill",
    description: "Load a skill by name...",
    handler: (args) => loadSkillContent(findSkill(args.skill))
};
```

### Automatic Skill Saving

A WorkBuddy design principle: **after a multi-step task, capture the workflow as a skill**. In this chapter the model proposes the save through `SkillCreate`, and every write is audited first.

### Write-Boundary Audit and Pending Approval

`SkillCreate` lets the model propose a write, but the decision is not the model's:

1. title, summary, and every read_when item must be single-line text without `---`, and values are written into frontmatter as JSON-quoted scalars. The assembled SKILL.md is parsed once, and audit, pending approval, and indexing share the single parsed permissions object; a mismatch with the request is rejected.
2. If `audit_skill()` returns P0 the skill is rejected; P1 (network/install commands, or requested network or write-path permissions) goes into the in-memory `pending_skills` with a "pending user approval" reply; P2 goes straight into the index.
3. The tool schema has no approval field. Only the user typing `approve <title>` / `reject <title>` in the terminal can resolve a pending item. approve re-audits and rejects only on P0; if a same-name skill entered the index meanwhile, it rejects and clears the pending item. Submitting the same name again returns "already pending" without overwriting.
4. `SkillCreate` goes through `authorize_loaded_skill_tool()` like every other tool: with no loaded skill the base policy applies; once skills are loaded, at least one loaded manifest must declare `SkillCreate` (the teaching version takes the union of loaded skills). Skill permissions narrow rather than widen, so to save skills inside a skill flow, declare `SkillCreate` in the manifest.

This is a single-process, in-memory simplification: pending items vanish when the process exits. A real harness persists pending items and asks the user to confirm through a UI.

### Code Walkthrough

## Code Walkthrough

Follow discovery, frontmatter indexing, trigger matching, deferred loading, permission checking, and tool execution.

## Run

```bash
python s16_skills_system/code.py
python -m pytest -q tests/test_skill_permissions.py
```

## Exercises

Use these exercises to change one part of skill discovery, frontmatter catalogs, deferred loading, and auditing at a time and explain the resulting contract.

## Next Lesson

- A skill combines frontmatter, instructions, optional scripts, and explicit permissions. Indexing stays small; full content is loaded on demand.

**Reference tables**

| Stage | What happens | Loaded amount |
|------|-------|-------|
| Index | Scan frontmatter and build the catalog | About 50 tokens per skill |
| Match | Compare user input with trigger phrases | 0 tokens |
| Load | Read the complete matching `SKILL.md` | About 500-5000 tokens per skill |

| Component | Purpose | Observed references |
|------|------|------|
| `deferLoading` | Marks a tool for deferred loading | On tool definitions |
| `indexDeferredTool()` | Creates a lightweight index entry | Called during registration |
| `ToolSearch` | Keyword search | 17 references |
| `DeferExecuteTool` | Loads the schema and executes a deferred tool | 12 references |
| `getToolDescriptionFromProduct()` | Gets the full description on demand | Called before execution |

| Risk level | Meaning | Handling |
|------|------|------|
| P0 | Contains dangerous operations such as `rm -rf`, `sudo`, or `eval` | Reject installation |
| P1 | Contains network or installation operations such as `curl` or `npm install` | Ask the user |
| P2 | No detected dangerous pattern | Install directly |

The original Chinese README remains the primary-language reference. This English companion keeps the same code, diagrams, local paths, and chapter structure so readers can switch languages without losing runnable details.

Local references:

- [Reference](README.md)
- [Reference](README.en.md)
- [Reference](./images/skills-system-en.svg)
- [Reference](../docs/skill-evolution-and-evaluation.md)
- [Reference](../docs/security-boundaries.md)
