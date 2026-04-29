# Codex-Style Harness Roadmap

This document replaces the previous harness plan with an implementation roadmap specifically aimed at making the Paperbase chat harness behave much more like the open-source Codex runtime.

This is an execution plan for parallel Codex agents. It is not a product vision doc.

## Ground Truth

The target is based on the open-source `openai/codex` repository, not guesswork.

Observed Codex behaviors that matter here:

- one base instruction set is sent with each model call
- the Responses API request is built with `input`, `tools`, `parallel_tool_calls`, and `tool_choice: "auto"`
- the model is generally trusted to decide whether to call tools
- core tool exposure is broad and stable
- deterministic filtering still exists for some external tool surfaces, especially connector/app exposure and large MCP tool catalogs
- tool schemas and tool outputs are structured and stable

Key reference points from Codex:

- prompt assembly: `codex-rs/core/src/session/turn.rs`
- Responses request builder: `codex-rs/core/src/client.rs`
- prompt type: `codex-rs/core/src/client_common.rs`
- MCP tool exposure: `codex-rs/core/src/mcp_tool_exposure.rs`
- tool registry/spec assembly: `codex-rs/core/src/tools/spec.rs`

## What Must Change In Paperbase

Today Paperbase does too much app-side routing before the model gets a chance to act.

Current behavior we want to remove or sharply reduce:

- keyword-based decisions about whether to offer research tools
- keyword-based decisions about whether to offer project code tools
- forced `tool_choice={"type": "any"}` loops for normal turns
- prompt branching that tries to steer the model too aggressively before tool use
- different logical harnesses for "research turns" vs "code turns" before the model sees the tool surface

Target behavior:

- one default system prompt for normal turns
- a broad stable tool surface exposed up front
- `tool_choice=auto`
- fixed input and output contracts for every tool
- model-driven iteration through tool calls and tool results
- deterministic gating only where clearly justified by scale, permissions, or explicitly selected app surfaces

## Non-Goals

- matching every Codex internal feature
- implementing subagents as part of this first rewrite
- preserving current Paperbase prompt heuristics if they conflict with the Codex-style harness direction
- keeping backward compatibility for tests that encode the old keyword-gated behavior

## Success Criteria

The rewrite is successful when all of the following are true:

- `paperbase/src/paperbase/service.py` no longer decides tool availability from user-message keywords for the core Paperbase tools
- the model always sees one stable base prompt for normal turns
- the model always receives the core Paperbase tool set without app-side routing by turn type
- tool calls use fixed schemas and tool results use fixed structured payloads
- the model is not forced into tool use with `tool_choice={"type": "any"}` for ordinary turns
- the final answer is still generated from model-visible conversation and tool results, not from handwritten app-side summaries
- tests reflect the new harness rather than the old deterministic gating

## Design Rules

- Prefer one broad core tool set over multiple selectively offered tool sets.
- Keep tool definitions stable enough that the model can learn reusable habits.
- Keep tool outputs compact, structured, and deterministic.
- Do not hide core Paperbase capabilities just because a keyword heuristic did not fire.
- Do not add new orchestration complexity while removing the old routing logic.
- Only keep deterministic filtering where Codex also clearly does something similar:
  - connector or app enablement
  - config allowlists or blocklists
  - very large external tool catalogs that should be deferred behind a search tool

## Scope Boundaries

For this roadmap, "core tools" means:

- Paperbase research tools
- uploaded-document tools
- project code tools

For this roadmap, "external tools" means:

- future connector or app surfaces
- very large MCP-style catalogs if we add them later

The first rewrite should make the core Paperbase tools always available to the model.

## Primary Workstreams

These workstreams are designed so multiple Codex agents can work in parallel with minimal overlap.

### Workstream 1: Remove Keyword-Gated Tool Exposure

Owner:

- `paperbase/src/paperbase/service.py`
- `paperbase/tests/test_service.py`

Required changes:

- remove or bypass `_should_offer_research_chat_tools`
- remove or bypass `_should_offer_project_code_tools`
- remove or bypass `_should_force_chat_tool_use`
- stop building tool sets from request-content heuristics
- make `_chat_tool_definitions` return the stable core tool set for normal turns

Acceptance criteria:

- ordinary turns expose the same core tool set regardless of whether the prompt contains "agent", "code", "search", or similar trigger words
- tests no longer assert keyword-based exposure behavior
- tests verify that core tools are present across turn types

Notes:

- this is the single biggest behavioral change
- do not combine it with prompt rewrites in the same patch if that makes testing unclear

### Workstream 2: Collapse To One Default Tool Prompt

Owner:

- `paperbase/src/paperbase/service.py`
- `paperbase/tests/test_service.py`

Required changes:

- replace the current tool-prompt branching with one default normal-turn system prompt
- keep compaction-specific prompting separate
- keep final-answer prompting separate only if needed for formatting and citation behavior
- remove research-turn vs code-turn prompt routing as a gating mechanism

Acceptance criteria:

- one stable system prompt is used for normal tool-enabled turns
- that prompt describes the available tools and expected behavior without keyword-based branching
- tests verify prompt stability across substantially different user prompts

Notes:

- "one default prompt" does not mean one string for every system path in the entire product
- compaction can remain specialized
- review mode can remain specialized if it is a separate runtime mode

### Workstream 3: Normalize Tool I/O Contracts

Owner:

- `paperbase/src/paperbase/service.py`
- `paperbase/tests/test_service.py`
- `paperbase-plugin/tests/test_plugin.py` if plugin tool payloads need alignment

Required changes:

- audit every tool input schema
- audit every tool result payload
- make tool outputs consistent in shape and naming
- ensure errors are returned in a fixed structure
- make payloads compact and model-facing rather than UI-facing

Acceptance criteria:

- each tool has one documented input contract
- each tool has one stable success payload shape
- each tool has one stable error payload shape
- tests assert exact output shapes for representative tools

Notes:

- this should not materially change product capability
- it should make the model easier to train against by consistency

### Workstream 4: Remove Forced Tool-Choice Steering

Owner:

- `paperbase/src/paperbase/service.py`
- `paperbase/tests/test_service.py`

Required changes:

- stop using `tool_choice={"type": "any"}` for ordinary turns
- default to `auto`
- remove retry loops whose sole purpose is forcing a write because the app pre-decided the turn should be a code-writing turn
- keep narrow failure handling only where the tool call itself failed and a retry is explicitly justified

Acceptance criteria:

- normal turns use tool auto-selection
- tests no longer expect sequences of `{"type": "any"}` calls
- the harness still completes code tasks successfully through model-driven tool use

Notes:

- this does not forbid future hard requirements for special modes
- for the main harness, the model should be trusted first

### Workstream 5: Rebuild Tests Around The New Harness

Owner:

- `paperbase/tests/test_service.py`
- any affected repo-policy tests

Required changes:

- delete or rewrite tests that encode keyword-gated behavior
- add tests for broad stable tool exposure
- add tests for `tool_choice=auto`
- add tests for fixed tool payloads
- add tests showing the model can choose not to use tools
- add tests showing the model can choose tools when useful without the app pre-routing the turn

Acceptance criteria:

- test names and assertions describe the new harness honestly
- no failing tests rely on the old deterministic routing assumptions
- smoke coverage still protects the new behavior

Notes:

- this workstream should start early so the rewrite has a clear target

## Secondary Workstreams

These are useful after the main simplification lands.

### Workstream 6: Add A Tool Catalog Deferral Pattern

This is the one deterministic gating pattern we should preserve if needed, because Codex does something similar for large MCP catalogs.

Owner:

- `paperbase/src/paperbase/service.py`
- future tool-search implementation and tests

Required changes:

- if the tool surface ever becomes too large, defer part of it behind a search tool instead of keyword gating
- keep core Paperbase tools directly exposed
- only defer very large optional tool families

Acceptance criteria:

- no keyword routing is reintroduced
- large tool catalogs can be discovered through a dedicated tool-search path

### Workstream 7: Align Compaction With Model-Visible History

Compaction is allowed to stay specialized, but it should not depend on handwritten harness summaries that diverge from the model-visible record.

Owner:

- `paperbase/src/paperbase/service.py`
- compaction-related tests

Required changes:

- keep compaction model-driven
- preserve tool call and tool result continuity
- ensure the post-compaction conversation still reflects what the model actually saw

Acceptance criteria:

- compacted conversations remain tool-usable
- no hidden app-side state is required for the model to continue

## Recommended Parallel Agent Plan

Use separate Codex agents with disjoint file ownership where possible.

Agent A:

- own tool exposure simplification in `paperbase/src/paperbase/service.py`
- own test rewrites for exposure behavior in `paperbase/tests/test_service.py`

Agent B:

- own prompt simplification in `paperbase/src/paperbase/service.py`
- own prompt-related tests in `paperbase/tests/test_service.py`

Agent C:

- own tool I/O normalization in `paperbase/src/paperbase/service.py`
- own tool payload assertions in `paperbase/tests/test_service.py`

Agent D:

- own compaction alignment and any compaction tests

Coordination rules:

- Agent A lands first or rebases often because tool exposure affects nearly everything
- Agents B and C can work in parallel if they avoid the same helper functions
- Agent D should start after the prompt shape and tool payload shape stabilize

## Order Of Operations

1. rewrite tests to make the target explicit
2. remove keyword-gated tool exposure
3. collapse to one default prompt
4. remove forced tool-choice steering
5. normalize tool I/O
6. stabilize compaction around the simplified harness
7. run full validation and remove dead helper code

## Deletions We Expect

The rewrite should probably delete or sharply reduce logic in these areas:

- request-content heuristics for tool offering
- request-content heuristics for forced tool usage
- prompt text that exists only to compensate for app-side routing
- tests asserting the old turn classification behavior

Dead-code cleanup is part of the job, not optional polish.

## Deliverables

The implementation is complete when all of the following exist:

- a simplified `service.py` harness with broad stable tool exposure
- updated tests proving the new behavior
- updated docs if developer workflow or debugging expectations changed
- a short note in this file or `README.md` describing the new harness model

## Definition Of Done

We should be able to describe the harness in one sentence like this:

Paperbase sends one default system prompt, exposes its core tool set up front with fixed schemas and outputs, uses `tool_choice=auto`, and mostly trusts the model to decide how to work.

If that sentence is not true yet, the rewrite is not done.
