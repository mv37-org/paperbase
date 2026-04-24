# Roadmap

This roadmap is intentionally concrete and PyTorch-first.

The product direction is:

- the agent writes PyTorch-based ML code from paper knowledge and user direction
- Paperbase visualizes that code as a workflow
- workflow steps expose checkpoints, telemetry, and benchmarkable artifacts
- workflows run in Modal first, with more runtimes added later
- the system infers the intent of workflow components, searches for relevant new papers, and proposes experiments
- users stay in control of approving, denying, running, and promoting experiments

## Phase 1: Agent Writes PyTorch Code

- Make the agent reliably generate PyTorch-centric code for common ML tasks.
- Start with a narrow set of workflows that matter most: training, fine-tuning, evaluation, and data preparation.
- Make the agent parameterize generated code so the same workflow can run in smoke-test mode or full-run mode.
- Keep the first version biased toward code the system can understand later, not arbitrary free-form scripts.

## Phase 2: Visualize PyTorch Code As A Workflow

- Build a workflow view that maps generated PyTorch code into a flowchart.
- Represent major components such as data loading, transforms, training, evaluation, and custom Python steps as nodes.
- Treat the workflow view as a real product surface, not a decorative graph.
- Ensure the graph stays aligned with the code the agent writes.

## Phase 3: Add Checkpoints And Telemetry

- Add checkpoints at meaningful stages of the workflow.
- Add telemetry for metrics, logs, runtime, cost, artifacts, and failures.
- Make these checkpoints and telemetry outputs visible from the home view.
- Use this layer to support benchmarking, debugging, and baseline comparison.

## Phase 4: Run Workflows In Modal

- Execute workflows in Modal sandboxes first.
- Support smoke tests, partial runs, retries, resume, and full runs.
- Persist run state, artifacts, and checkpoint outputs outside the sandbox lifecycle.
- Keep the runtime layer abstract enough to support additional backends later.

## Phase 5: Infer Component Intent

- Use LLMs to infer the intent of important workflow components and code regions.
- Identify what each part of the workflow is doing, such as data loading, SFT, evaluation, RL, or post-processing.
- Use that inferred intent to improve visualization, search, experiment generation, and future automation.
- Keep intent inference grounded in actual code structure, not just natural-language guesses.

## Phase 6: Search Papers And Propose Experiments

- Use inferred workflow intent to drive targeted paper searches.
- Search for new papers and methods relevant to specific workflow components and user goals.
- Turn promising ideas into concrete experiment proposals tied to workflow diffs.
- Make proposals specific enough to run: what changes, why it might help, and how it will be evaluated.

## Phase 7: Approval, Execution, Comparison, And Promotion

- Show experiment proposals to the user for approval or denial.
- If approved, run experiments immediately against the current baseline workflow.
- Compare experiment checkpoints, telemetry, metrics, artifacts, and outcomes against the baseline.
- Let the user promote a successful experiment to become the main workflow.
- Keep the promotion path explicit and reversible.

## Longer-Term Direction

- Expand beyond Modal to more runtimes.
- Support broader PyTorch-adjacent workflows such as distillation, RL, agent eval harnesses, and more complex fine-tuning setups.
- Increase autonomy over time, but keep expensive or high-impact changes approval-gated.
