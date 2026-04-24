# Roadmap

Paperbase is moving from a grounded paper-memory and code-execution tool into a workflow-first research system for ML and AI work.

The core product direction is:

- use papers, project context, and user instructions to generate ML workflows
- represent workflows as typed nodes, not opaque scripts
- keep the workflow layer above a PyTorch-centric training stack rather than replacing PyTorch itself
- run workflows on Modal first, with more runtimes later
- make experiments, comparisons, and promotion first-class
- keep humans in the approval loop as autonomy increases

## Guiding Principles

- The workflow graph is the source of truth.
- PyTorch remains a primary execution substrate for training, data, and checkpoints.
- Paperbase defines higher-level workflow semantics on top of PyTorch and related tooling.
- Python remains an escape hatch, not the default abstraction.
- Every step should declare inputs, outputs, parameters, resources, and artifacts.
- Smoke-test mode must be platform-level, not prompt-only.
- Autonomy must be eval-driven and budget-governed.

## Phase 1: Workflow Foundation

- Introduce a typed workflow IR and SDK.
- Start with a small set of node types: data load, transform, train, eval, compare, export, custom Python.
- Define the initial node implementations around a PyTorch-centric stack, with room for Lightning, Hugging Face, and related training libraries behind the same workflow model.
- Add workflow visualization in the product so agent-authored code maps cleanly to a graph.
- Add step-level resource declarations, checkpoint policies, and smoke-test parameters.

## Phase 2: Execution And Artifacts

- Run workflow steps on Modal through a consistent execution layer.
- Persist logs, metrics, artifacts, and checkpoints per node and per run.
- Surface checkpoints and run state in the home view.
- Support retry, resume, cancel, and rerun from checkpoint.

## Phase 3: Experiments And Promotion

- Make baseline workflows and candidate experiments first-class objects.
- Let users approve or deny experiment runs before execution.
- Compare candidate vs baseline on metrics, artifacts, cost, runtime, and traces.
- Let users promote a winning experiment to become the main workflow.

## Phase 4: Paper-Grounded Proposals

- Use Paperbase retrieval, latest-paper sync, and project context to generate improvement proposals.
- Tie every proposal to cited papers plus a concrete workflow diff and eval plan.
- Rank proposals by expected upside, cost, and risk.
- Keep execution approval-based by default.

## Phase 5: Broader ML Support

- Add higher-level workflow templates for SFT, distillation, fine-tuning, RL, and agent eval harnesses, implemented first against the PyTorch ecosystem.
- Add stronger dataset, model, and checkpoint lineage.
- Expand beyond Modal to additional runtimes and schedulers.
- Add richer result inspection for eval failures, slices, and qualitative traces.

## Phase 6: Bounded Autonomy

- Allow the system to monitor workflows and generate proposals continuously.
- Allow low-risk smoke tests to run automatically under explicit budgets and policies.
- Keep expensive runs, promotions, and production-impacting changes human-approved.
- Move toward an autonomous research assistant that is grounded in papers, code, experiments, and eval history.
