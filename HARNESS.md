# Agent Harness Roadmap

This document lays out the runtime roadmap for moving the Paperbase agent closer to the behavior quality of Codex-style coding agents.

The goal is not to imitate Codex prompt text. The goal is to build a stronger agent harness: explicit state, richer tool orchestration, better recovery, stronger verification, and clearer completion criteria.

## Why This Exists

Today the Paperbase agent is a relatively thin loop around the model:

1. build a tool prompt
2. call the model with tools
3. execute any returned tool calls
4. append tool results
5. repeat for a few rounds
6. generate a final answer in a separate call

That loop works, but it is still simpler than the runtime shape of Codex-style agents. The main gap is not one missing prompt. The main gap is that our harness does not yet maintain enough explicit execution state or enforce enough structured intermediate behavior.

Current implementation anchors:

- tool loop: `paperbase/src/paperbase/service.py`
- model client: `paperbase/src/paperbase/llm.py`
- behavior tests: `paperbase/tests/test_service.py`

## Current State

The current agent already has a few useful enforcement mechanisms:

- a strong tool-use system prompt
- forced tool usage on certain turns with `tool_choice={"type": "any"}`
- forced persisted writes for project-code turns
- a retry prompt when no managed project file was actually updated
- a separate final-answer pass after tool rounds

Those pieces are good, but they are still mostly prompt-and-loop enforcement. The runtime does not yet behave like a full agent harness with explicit planning, verification, repair, and completion gating.

## Target State

We want the harness to behave more like a disciplined engineering runtime:

- it tracks the task as structured state, not only as chat history
- it knows what phase it is in: triage, inspect, edit, verify, finalize
- it gives the model different guidance at different phases
- it reacts differently to different tool failures
- it requires verification before claiming success
- it can explain why the task is done in terms of explicit completion checks

## Design Principles

- Build runtime behavior in the harness, not only in the prompt.
- Keep prompts short and phase-specific instead of one monolithic instruction block.
- Prefer tool-level and state-level enforcement over natural-language nudges.
- Treat code work as incomplete until inspection, edit, and verification all succeed.
- Preserve room for Paperbase-specific strengths: research grounding, citations, and project-scoped code tools.
- Do not try to mimic hidden Codex internals exactly. Reproduce the useful external properties.

## Non-Goals

- Recreating Codex's exact internal prompting
- Replacing Paperbase's research-first identity with a generic coding shell
- Expanding into arbitrary local filesystem control before the harness logic is ready
- Optimizing for maximum autonomy before verification and auditability are strong

## Phase 1: Introduce Explicit Agent State

Add a structured per-turn harness state object that is maintained outside the raw transcript.

Minimum state:

- user goal
- task type: research, coding, mixed, review
- current phase
- active plan
- files inspected
- files edited
- verification status
- completion criteria
- pending blockers
- last tool failure and recovery path

Implementation direction:

- add a turn-scoped state structure in `paperbase/src/paperbase/service.py`
- persist only what must survive compaction; keep the rest ephemeral per turn
- make the loop consult state instead of inferring everything from prior messages

Acceptance criteria:

- the harness can identify its current phase without re-parsing the whole transcript
- the final answer can be gated on explicit completion flags
- tests prove that state survives multiple tool rounds and compaction

## Phase 2: Replace One Prompt With Phase-Specific Prompts

Split the current generic tool prompt into smaller prompts keyed to harness phase.

Phases:

- triage
- discovery
- inspection
- edit
- verification
- repair
- final answer

Examples:

- triage prompt decides whether this is a research turn, code turn, or mixed turn
- inspection prompt forces read-first behavior before edit attempts
- repair prompt explains why the previous tool attempt failed and what to do next
- verification prompt forbids success claims until checks pass

Implementation direction:

- replace one reusable tool system prompt builder with prompt builders by phase
- inject narrow runtime facts with each phase prompt
- keep final-answer prompting separate from tool-round prompting

Acceptance criteria:

- the harness uses different prompt text for different phases
- failed edits trigger repair prompts that are specific to the failure mode
- tests prove that the model gets different system prompts across a realistic turn

## Phase 3: Add a Real Planner and Done Criteria

Introduce a lightweight hidden plan before substantial tool work.

The plan should not be free-form chain-of-thought. It should be structured runtime state with fields such as:

- objective
- substeps
- required tools
- success checks
- likely risks

The harness should also generate explicit done criteria. Examples:

- target file was found and read
- requested edit was applied
- smoke validation passed
- final answer references the saved file path

Implementation direction:

- add a planner pass for non-trivial tasks
- let the planner write into harness state
- make the executor consume that state rather than improvising every round

Acceptance criteria:

- non-trivial code tasks create a structured plan before edits
- the harness refuses to finalize if done criteria are not met
- tests cover incomplete-edit, incomplete-verify, and complete-success cases

## Phase 4: Improve the Tool Surface

Codex-style behavior benefits from richer tools. Our current tool surface is still narrow, especially for project code.

Priority additions:

- stronger patch/edit primitive beyond exact string replacement
- project test tool
- project lint tool
- project git-status tool
- project diff tool
- project inspect macro for list plus grep plus read workflows

Secondary additions:

- line-oriented edit support with tighter ambiguity handling
- richer run outputs with normalized traceback and artifact summaries
- optional repo-wide search across more than managed Python when safe

Implementation direction:

- expand `project_*` tooling in `paperbase/src/paperbase/service.py`
- prefer small compound tools for common workflows over forcing the model to compose every micro-step
- keep tool outputs compact and stable for model consumption

Acceptance criteria:

- the agent can inspect, patch, validate, and summarize a code change without falling back to chat-only reasoning
- common edit failures no longer require awkward exact-match retries
- tests cover patching, lint/test execution, and diff-aware finalization

## Phase 5: Add Verification as a First-Class Stage

Verification should become mandatory for meaningful code work.

Verification modes:

- structural verification: file exists, edit applied, imports resolve if cheaply checkable
- behavioral verification: smoke run, test subset, lint, or targeted checks
- answer verification: final response accurately reflects what happened

Implementation direction:

- add a dedicated verification phase after edit
- let the harness select the cheapest sufficient validation for the task
- persist verification results into turn state

Examples:

- doc-only change: no tests, but file existence and content check
- small code edit: lint or targeted test
- workflow change: `make test`
- performance-sensitive path: `make profile`

Acceptance criteria:

- the harness does not claim code success without a recorded verification result
- the final answer distinguishes verified work from unverified work
- tests cover both successful verification and verification-skipped cases

## Phase 6: Add Tool-Failure-Specific Repair Logic

Right now repair is mostly generic. We need failure-aware recovery.

Repair categories:

- search too broad
- search returned no evidence
- file not found
- edit target ambiguous
- exact-match edit failed
- run failed with traceback
- verification failed
- write attempted but no meaningful project change occurred

Implementation direction:

- normalize tool failures into structured failure types
- map each type to a repair prompt and preferred next tools
- record the failure in harness state so repeated bad retries are less likely

Acceptance criteria:

- repeated identical failing tool calls become less common
- the harness chooses a repair strategy based on failure type
- tests cover edit failure, missing file, and failed run recovery

## Phase 7: Improve Memory and Compaction

Raw transcript replay is not enough as turns get longer.

The harness should preserve structured facts across compaction:

- active goal
- inspected files
- chosen target files
- evidence gathered
- edits already completed
- verification outcomes
- unresolved blockers

Implementation direction:

- keep the existing compaction path, but add a structured handoff summary format
- store compact state separately from user-visible chat text when possible
- make compaction preserve actionable execution state, not just prose summary

Acceptance criteria:

- long multi-round tasks retain execution continuity after compaction
- the harness does not forget which files were already read or edited
- tests simulate compaction in the middle of a code task

## Phase 8: Add Self-Review and Finalization Gates

Before the final answer, run a small critic pass over the harness state and execution record.

Critic questions:

- did we inspect before editing
- did we edit the correct file
- did verification happen
- are we claiming success without evidence
- are citations grounded
- did we leave code only in chat text

Implementation direction:

- add a pre-finalization review pass
- give it read-only access to state, tool results, and pending answer draft
- allow it to send the turn back to repair or verification if needed

Acceptance criteria:

- finalization can be blocked by the critic
- obvious false-success cases are caught before the user sees them
- tests cover bad-answer interception

## Phase 9: Build an Evaluation Harness

We need a benchmark for agent behavior, not just low-level tool correctness.

Scenario categories:

- research-only question
- grounded research comparison
- code creation from scratch
- code edit in existing file
- edit after failed first attempt
- mixed research plus code task
- verification-required task
- long context with compaction

Metrics:

- task completion rate
- verification rate
- false success rate
- unnecessary tool call rate
- repeated failure loop rate
- citation correctness rate
- file-targeting accuracy

Implementation direction:

- add scenario-style tests on top of `paperbase/tests/test_service.py`
- capture harness-state transitions, not only final text
- add benchmark fixtures for regression tracking

Acceptance criteria:

- we can measure harness quality over time
- runtime changes can be compared before and after
- false-success regressions are caught automatically

## Recommended Build Order

1. explicit state machine
2. phase-specific prompts
3. planner plus done criteria
4. verification phase
5. failure-specific repair
6. stronger edit and validation tools
7. compaction with structured state preservation
8. critic pass
9. scenario evaluation harness

This order matters. Tool expansion without stronger state and verification will increase surface area without reliably improving behavior.

## First Implementation Slice

The first concrete slice should stay narrow:

- add a harness state object
- add phase tracking
- split tool prompting into at least `triage`, `execute`, `repair`, and `verify`
- require verification for meaningful code turns
- add tests for phase transitions and incomplete verification blocking

Suggested files:

- `paperbase/src/paperbase/service.py`
- `paperbase/src/paperbase/llm.py`
- `paperbase/tests/test_service.py`

## Definition of "Closer to Codex"

We should consider this effort successful when the Paperbase agent consistently shows these external behaviors:

- it inspects before editing
- it changes course after failures instead of blindly repeating
- it verifies meaningful changes before claiming success
- it maintains execution continuity across long turns
- it produces final answers that reflect actual tool outcomes
- it behaves like a reliable engineering assistant, not a prompt with tools attached

That is the real target.
