# Codex-Style Harness Task Board

This file is the parallel execution board for rewriting the Paperbase harness to behave more like the open-source Codex runtime.

It is meant to be actively used by multiple Codex tabs working on separate branches and separate worktrees.

## Target

We are moving Paperbase toward this harness model:

- one default system prompt for normal turns
- broad stable core tool exposure up front
- `tool_choice=auto`
- fixed tool schemas and fixed tool result shapes
- model-driven tool use
- deterministic gating only for explicit app surfaces, config allowlists or blocklists, and very large optional tool catalogs

## Ground Truth From Codex

Reference implementation:

- prompt assembly: `codex-rs/core/src/session/turn.rs`
- request builder: `codex-rs/core/src/client.rs`
- prompt type: `codex-rs/core/src/client_common.rs`
- tool exposure: `codex-rs/core/src/mcp_tool_exposure.rs`
- tool registry: `codex-rs/core/src/tools/spec.rs`

Relevant Codex properties:

- the model request carries one instruction block, `input`, `tools`, `parallel_tool_calls`, and `tool_choice: "auto"`
- core tools are broadly exposed
- connector and app tools are still filtered deterministically
- large MCP tool catalogs are deferred behind a search tool rather than exposed wholesale

## Hard Rules

- Only one tab owns `HARNESS.md`.
- Every worker gets its own branch.
- Every worker should ideally get its own `git worktree`.
- Workers must have disjoint write scopes whenever possible.
- If two workers touch the same file, they must own different functions or sections and merge often.
- The integration tab is the only tab that merges worker branches into the main line.

## Status Board

`[ ]` not started
`[-]` in progress
`[x]` complete

## Coordinator Lane

Owner:

- one human-operated tab or one designated Codex integration tab

Responsibilities:

- keep this file current
- create branches and worktrees
- assign workers
- integrate small batches
- run validation after each merge batch
- resolve conflicts

Status:

- [ ] create worktrees
- [ ] assign workers
- [ ] merge worker A
- [ ] merge worker B
- [ ] merge worker C
- [ ] merge worker D
- [ ] run `make test`
- [ ] final cleanup pass

## Worker A

Branch:

- `codex/harness-tool-exposure`

Write scope:

- `paperbase/src/paperbase/service.py`
- `paperbase/tests/test_service.py`

Mission:

- remove keyword-gated core tool exposure
- stop deciding core tool availability from request content
- make the normal-turn core Paperbase tool set broadly and stably exposed

Primary targets:

- `_should_offer_research_chat_tools`
- `_should_offer_project_code_tools`
- `_should_force_chat_tool_use`
- `_chat_tool_definitions`

Deliverables:

- code no longer routes core tool exposure through keyword heuristics
- tests rewritten to assert stable core tool presence

Dependencies:

- none

Status:

- [ ] branch created
- [ ] implementation started
- [ ] tests updated
- [ ] local relevant tests run
- [ ] ready for merge

## Worker B

Branch:

- `codex/harness-single-prompt`

Write scope:

- `paperbase/src/paperbase/service.py`
- `paperbase/tests/test_service.py`

Mission:

- collapse normal-turn tool prompting to one default prompt
- remove prompt branching used only to compensate for app-side routing

Primary targets:

- `_chat_tool_system_prompt`
- any helper branches that separate research and code turn prompting for normal turns

Deliverables:

- one stable default normal-turn prompt
- tests proving prompt stability across different user prompts

Dependencies:

- should rebase on Worker A before final merge

Status:

- [ ] branch created
- [ ] implementation started
- [ ] tests updated
- [ ] local relevant tests run
- [ ] ready for merge

## Worker C

Branch:

- `codex/harness-tool-io`

Write scope:

- `paperbase/src/paperbase/service.py`
- `paperbase/tests/test_service.py`
- `paperbase-plugin/tests/test_plugin.py` if needed

Mission:

- normalize tool input and output contracts
- make tool result payloads consistent and model-facing
- make tool errors structurally consistent

Primary targets:

- tool definitions in `_chat_tool_definitions`
- payload shapes in `_execute_chat_tool`
- tool result handling in `_tool_result_blocks`

Deliverables:

- fixed success payload shape per tool
- fixed error payload shape per tool
- tests asserting exact shapes

Dependencies:

- none

Status:

- [ ] branch created
- [ ] implementation started
- [ ] tests updated
- [ ] local relevant tests run
- [ ] ready for merge

## Worker D

Branch:

- `codex/harness-tool-choice-auto`

Write scope:

- `paperbase/src/paperbase/service.py`
- `paperbase/tests/test_service.py`

Mission:

- remove forced `tool_choice={"type":"any"}` behavior for ordinary turns
- default to model-driven `auto`
- delete retry logic that exists only because the app pre-decided the model must write code

Primary targets:

- `_run_chat_tool_rounds`
- `_run_chat_tool_rounds_stream`
- any write-forcing retry helpers that no longer make sense

Deliverables:

- ordinary turns run with `auto`
- tests no longer expect `{"type":"any"}` sequences

Dependencies:

- should rebase on Worker A
- should rebase on Worker B if prompt logic changes touch the same code path

Status:

- [ ] branch created
- [ ] implementation started
- [ ] tests updated
- [ ] local relevant tests run
- [ ] ready for merge

## Worker E

Branch:

- `codex/harness-compaction-alignment`

Write scope:

- `paperbase/src/paperbase/service.py`
- compaction-related tests

Mission:

- keep compaction specialized if needed
- ensure compaction remains model-visible and compatible with the simplified harness
- avoid hidden handwritten app-side state that the model cannot see

Primary targets:

- `_ensure_chat_context_budget`
- `_ensure_chat_context_budget_stream`
- compaction prompt and compaction tests

Deliverables:

- compaction still works after the harness simplification
- tests prove continuity across compacted conversations

Dependencies:

- should start after Workers A, B, and D have stabilized the main harness path

Status:

- [ ] branch created
- [ ] implementation started
- [ ] tests updated
- [ ] local relevant tests run
- [ ] ready for merge

## Merge Order

Use this order unless reality forces a different one:

1. Worker A
2. Worker C
3. Worker B
4. Worker D
5. Worker E
6. integration cleanup

Why this order:

- Worker A changes the harness shape most
- Worker C can normalize payloads once exposure is stable
- Worker B should land after the new exposed surface is clear
- Worker D should remove forcing only after the stable prompt and tool surface are in place
- Worker E should adapt compaction after the core harness path settles

## Integration Checklist

For every worker branch:

- [ ] fetch latest main branch state
- [ ] rebase worker branch
- [ ] inspect diff for accidental overlap
- [ ] merge or cherry-pick into integration branch
- [ ] run targeted tests
- [ ] commit integration result

Before final merge:

- [ ] run `make test`
- [ ] update this board statuses
- [ ] remove dead helper functions
- [ ] remove tests that only encoded the old heuristic harness

## Suggested Worktree Setup

Run from the main checkout:

```bash
git worktree add ../paper-ai-harness-a -b codex/harness-tool-exposure
git worktree add ../paper-ai-harness-b -b codex/harness-single-prompt
git worktree add ../paper-ai-harness-c -b codex/harness-tool-io
git worktree add ../paper-ai-harness-d -b codex/harness-tool-choice-auto
git worktree add ../paper-ai-harness-e -b codex/harness-compaction-alignment
git worktree add ../paper-ai-harness-integration -b codex/harness-integration
```

Then open a separate Codex tab in each worktree.

## Suggested Tab Assignments

Tab 1:

- coordinator or integration tab
- owns this file
- owns `codex/harness-integration`

Tab 2:

- Worker A
- worktree `../paper-ai-harness-a`

Tab 3:

- Worker B
- worktree `../paper-ai-harness-b`

Tab 4:

- Worker C
- worktree `../paper-ai-harness-c`

Tab 5:

- Worker D
- worktree `../paper-ai-harness-d`

Tab 6:

- Worker E
- worktree `../paper-ai-harness-e`

## Prompt To Give Each Worker

Use a prompt like this in each worker tab:

```text
You own only the write scope assigned in HARNESS.md for your worker lane.
Read HARNESS.md first, implement only your lane, do not broaden scope, do not edit HARNESS.md, commit when done, and report:
1. files changed
2. tests run
3. remaining merge risks
```

## Handoff Format

Every worker should report back in this format:

- branch name
- commit sha
- files changed
- tests run
- known conflicts
- whether the branch is ready to merge

## What Not To Do

- Do not run multiple Codex tabs against the same checkout and same branch.
- Do not let more than one tab edit `HARNESS.md`.
- Do not split the same function across two workers unless you want merge pain.
- Do not wait until all workers finish before integrating.
- Do not allow one branch to drift far from the integration branch.

## Definition Of Done

The harness rewrite is done when:

- core Paperbase tools are broadly exposed without keyword gating
- normal turns use one default prompt
- normal turns use `tool_choice=auto`
- tool contracts are stable
- tests describe the new harness honestly
- `make test` passes
