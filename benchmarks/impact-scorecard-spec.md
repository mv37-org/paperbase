# Paperbase Impact Scorecard Measurement Spec

Status: draft

Related:

- Issue [#62](https://github.com/mv37-org/paper-ai/issues/62): Paperbase impact scorecard umbrella
- Issue [#47](https://github.com/mv37-org/paper-ai/issues/47): extraction quality tracker
- Issue [#21](https://github.com/mv37-org/paper-ai/issues/21): real-user-query retrieval benchmark

## Purpose

This document specifies how to measure Paperbase impact scientifically enough that:

- small convenience samples do not masquerade as product truth
- regressions can still be caught quickly with canary sets
- representative product claims can be made with uncertainty bounds
- "no effect" claims are reserved for sufficiently powered studies

The scorecard is split into three layers:

1. indexing quality
2. retrieval quality
3. harness lift with vs without Paperbase

The layers must stay separate. A failure in one layer should not be hidden by wins in another.

## Research questions

The scorecard should answer six distinct questions:

1. When Paperbase indexes papers, how often does it produce rich and usable artifacts?
2. How often does the search stack return the right paper?
3. How often does the section-level stack return the right passage?
4. How much does Paperbase improve grounded task success in real harnesses?
5. What is the cost of those gains in latency, tool calls, and tokens?
6. How certain are we that the measured effects generalize beyond the sampled items?

## Measurement principles

### 1. Separate canaries from representative estimates

We need both:

- `Canary panels`: small, fixed, curated sets used to catch obvious regressions quickly
- `Representative panels`: sampled to estimate product-wide impact
- `Stress panels`: intentionally hard or rare cases, such as figure/table/code-heavy questions

Canary and stress panels are useful, but neither is representative by default.

### 2. Treat "absence of evidence" as inconclusive unless power is adequate

If the sample is small or the confidence interval is wide, the correct conclusion is:

- `inconclusive`

not:

- `no effect`

For each primary metric, implementation must define a minimally important effect size. If the confidence interval overlaps both zero and that effect size, the result is underpowered or inconclusive. Only an equivalence-style result can support "no practically meaningful difference."

### 3. Prefer time-based holdouts over random leakage

For retrieval and harness evaluations derived from real usage, holdouts should be split by time rather than only random rows. This reduces label leakage and reduces the chance that prompt or ranking changes indirectly tune against the same distribution they are later judged on.

### 4. Use matched comparisons whenever possible

For "with vs without Paperbase" evaluations:

- keep the model fixed
- keep the prompt fixed except for the Paperbase tool surface
- keep the task fixed
- run both conditions on the same task
- compare paired outcomes

Paired designs dramatically reduce noise relative to unmatched comparisons.

### 5. Publish uncertainty, not just point estimates

Every headline metric must be reported with:

- sample size
- time window
- confidence interval
- benchmark tier: canary, representative, or stress
- model and harness versions where applicable

### 6. Separate descriptive telemetry from causal claims

Some metrics describe system state:

- percent `abstract-only`
- median sections per paper
- p95 indexing latency

Some metrics support a causal claim:

- task success improved with Paperbase
- unsupported claims fell with Paperbase

Do not treat descriptive telemetry as causal evidence.

## Evaluation tiers

### Tier A: Canary

Purpose:

- detect obvious regressions during development

Properties:

- fixed item set
- small sample
- fast to rerun
- not representative

Allowed claims:

- "regression detected on the canary set"
- "no regression detected on the canary set"

Disallowed claims:

- "Paperbase improved overall user outcomes"
- "there is no meaningful difference"

Examples:

- the current 10-paper extraction audit in #47
- fixed landmark retrieval queries

### Tier B: Representative gate

Purpose:

- provide PR and release confidence on realistic data

Properties:

- stratified sample from the target population
- weighted back to the target population when oversampling rare strata
- large enough for stable directional comparisons

Allowed claims:

- "directionally improved on the representative gate set"
- "regressed on long-tail author queries"

Disallowed claims:

- strong external product claims unless the sample size and time coverage meet publishable thresholds

### Tier C: Publishable study

Purpose:

- support internal strategy memos and external communication

Properties:

- representative sample
- pre-declared primary metrics
- adequate temporal coverage
- explicit uncertainty and power checks
- adjudicated labels where needed

Allowed claims:

- "Paperbase improved task success by X points on the publishable evaluation set"
- "the study was inconclusive for figure-heavy tasks"

## Target populations and units of analysis

| Layer | Population | Primary unit | Secondary unit |
| --- | --- | --- | --- |
| Indexing quality | papers indexed in the study window | paper | index event |
| Retrieval quality | eligible real user search intents in the study window | canonical query intent | query impression |
| Harness lift | realistic user tasks for a fixed harness and model | task | task x seed x condition |

### Canonical query intent

Search traffic often contains bursty reformulations:

- `transform`
- `transformer`
- `transformer attention`

These should not necessarily count as three independent search intents. For representative retrieval studies, raw search logs should be collapsed into canonical query intents using a deterministic burst-collapsing rule.

Recommended v1 rule:

- same user or anonymous session
- same 30-second window
- later query is a strict extension, prefix variant, or minor edit of the earlier query
- retain only the terminal query in the burst for representative query-level estimates

Raw impression-level logs should still be kept for product telemetry.

## Data sources

### Existing sources in this repo

- paper metadata and `source_type`
- object-store manifests and extracted artifacts
- `text_chunks` and embedding presence
- `search_logs` with explicit user searches only
- explicit search click events
- benchmark outputs under `benchmarks/`

Important current rule:

- synthetic or tool-generated search reformulations must stay out of `search_logs`

That rule already exists in the codebase and must be preserved because synthetic queries would bias the representative query distribution.

### New instrumentation likely needed

Representative and publishable studies will benefit from additional events:

- result dwell or open duration
- paper drawer open events
- section expansion or evidence open events
- whether a retrieved paper was later cited in chat
- whether a task answer was accepted, copied, or abandoned

These are not required to define the metrics, but they improve preference labeling and downstream outcome measurement.

## Sampling design

### Indexing quality sampling

Indexing quality should combine a census-style telemetry layer with a human audit layer.

### Telemetry layer

Population:

- every paper indexed in the study window

Use telemetry for:

- source richness proportions
- artifact coverage
- embedding coverage
- freshness
- reliability

This is a census, not a sample, so sampling error is not the main risk. The main risks are instrumentation bugs and survivorship bias.

### Human audit layer

Purpose:

- evaluate readability, section fidelity, figure/table usefulness, and extraction cleanliness

Sampling:

- sample from all papers indexed in the window
- stratify by at least:
  - `source_type`
  - primary category family
  - publication year bucket
  - ingest mode when available, such as interactive vs backfill
- oversample rare but important strata like `pdf` and `abstract-only`
- record inclusion probabilities and weight back to the indexed-paper population

Recommended sample sizes:

- `Canary`: the existing 10-paper landmark set
- `Representative gate`: at least 150 audited papers
- `Publishable`: at least 400 audited papers over at least two non-adjacent collection windows

The 400-paper target is not magic. It is a practical floor for stable proportion estimates once stratification and weighting are introduced. If the weighted confidence intervals remain wide, the result is still inconclusive.

### Retrieval quality sampling

Retrieval measurement needs both a log-derived representative panel and a stress panel.

### Eligible query population

The target population is:

- explicit, user-initiated search intents
- in a declared time window
- excluding internal, synthetic, healthcheck, and tool-generated queries

Eligible queries should be deduplicated to canonical query intents for representative query-level estimates.

### Representative query sample

Sample from canonical query intents, not only from clicked queries.

Sampling strata should be based on observables available before human labeling:

- query length bucket
- query frequency bucket
- question vs keyword style
- presence of year tokens
- likely author-name tokens
- likely pasted-body-text queries
- user tenure or anonymous vs signed-in status if available

After sampling, label query intent for analysis:

- canonical paper lookup
- recent paper lookup
- author plus topic
- definitional or survey-style
- pasted passage
- comparison
- other

Recommended sample sizes:

- `Canary`: fixed query set
- `Representative gate`: at least 300 labeled canonical query intents
- `Publishable`: at least 1,000 labeled canonical query intents spanning at least four weeks

Each major intent bucket in a publishable study should have enough support to avoid collapsing into noise. A good floor is 100 labeled queries per common intent bucket when the traffic allows it.

### Stress query sample

Maintain a separate stress set for:

- typo-heavy queries
- acronym-only queries
- long pasted passages
- code-linked paper lookups
- figure/table-oriented questions
- long-tail paper lookups

Do not combine stress-set scores into representative product averages unless the weighting scheme is explicit.

### Harness lift sampling

Harness lift studies must combine naturalistic tasks and intentionally hard tasks.

### Representative task panel

Build from real product traces when possible:

- real chat tasks
- real coding-agent research tasks
- real browse-and-answer tasks

Sample tasks across:

- harness: Codex, Claude Code, chat UI
- task family
- task difficulty
- paper recency
- whether the task needs paper-level, section-level, figure/table, or code-linked evidence

### Stress task panel

Maintain separate panels for rare but strategically important tasks:

- figure interpretation
- table extraction
- code-linked paper analysis
- cross-paper comparison
- adversarially ambiguous or underspecified questions

### Recommended sample sizes

- `Canary`: small fixed panel
- `Representative gate`: at least 120 tasks per harness, with paired A/B runs
- `Publishable`: at least 300 tasks total, at least 75 per major task family, and at least 3 repeated runs per task-condition when the model output is stochastic

If a harness study is below that bar, it should be reported as exploratory or directional only.

## Labeling protocol

### General rules

- Every benchmark item must have a stable item id.
- Every item must store source window, sampling stratum, and inclusion probability.
- Labels must be versioned.
- Raters should be blinded to system variant whenever feasible.

### Retrieval labels

For each query:

- allow multiple acceptable papers when the intent is ambiguous
- label acceptable paper ids, not just one "best" answer
- for section retrieval, label one or more acceptable section paths or evidence spans
- record intent and ambiguity explicitly

Human labelers should not see the candidate system's ranking before writing the gold answer set. If candidate outputs are used to accelerate annotation, they should come from multiple systems pooled together and the gold set must allow newly surfaced acceptable answers after adjudication.

### Harness labels

For each answer:

- decompose the answer into atomic material claims when grounding is evaluated
- judge task success separately from stylistic polish
- mark each material claim as supported, unsupported, or unverifiable from the cited evidence
- judge whether cited evidence is directly relevant or only vaguely related

Recommended review process:

- two independent raters
- adjudication on disagreements
- report inter-annotator agreement

Agreement metrics:

- Cohen's kappa for binary or categorical judgments where applicable
- Krippendorff's alpha or equivalent for more complex labeling setups

## Metric definitions

### Indexing quality metrics

### 1. Source richness

Definition:

- weighted share of indexed papers by `source_type`

Primary derived metrics:

- `rich_index_rate = share(html + latex + pdf-docling)`
- `abstract_only_rate = share(abstract-only)`

Measurement:

- use telemetry on all indexed papers in the window
- report overall and by category, year bucket, and ingest mode

### 2. Artifact completeness

Definition:

- percent of indexed papers with:
  - at least one section
  - at least five sections
  - at least one figure caption
  - at least one table
  - a preview artifact

Also report:

- median sections per paper
- median text chunks per paper

Measurement:

- telemetry on all indexed papers
- weighted audit confirmation on the human-audit sample

### 3. Embedding coverage

Definition:

- share of indexed papers with a paper embedding
- share of text chunks with embeddings
- backlog count and rate for papers or chunks missing embeddings

Measurement:

- telemetry census
- separate paper-level and chunk-level rates

### 4. Extraction fidelity

Definition:

- human audit score on a 1-5 rubric for:
  - section boundary quality
  - prose cleanliness
  - figure usefulness
  - table usefulness
  - overall answerability from indexed artifacts

Primary summary:

- weighted mean rubric score
- percent of audited papers with score >= 4

The rubric must be frozen before publishable evaluations.

### 5. Freshness

Definition:

- time from paper discovery or indexing request to successful indexed state

Primary summary:

- p50, p95, and p99 latency

Measure separately for:

- interactive indexing
- scheduled backfills

### 6. Reliability

Definition:

- index success rate
- retry rate
- dead-letter rate
- successful reindex rate

Measurement window should be explicit:

- by day
- by week
- by release

### Retrieval quality metrics

### 1. Paper hit@k

Definition:

- fraction of labeled queries for which any acceptable paper appears in the top `k`

Required cutoffs:

- hit@1
- hit@3
- hit@5
- hit@10

Primary estimand:

- weighted hit@5 on the representative query panel

### 2. Section hit@k

Definition:

- fraction of labeled questions for which any acceptable evidence section appears in the top `k`

Required cutoffs:

- hit@1
- hit@3
- hit@5

Primary estimand:

- weighted section hit@5 on evidence-seeking queries

### 3. Clicked-rank MRR

Definition:

- reciprocal rank of the clicked result for queries with an explicit click
- averaged over eligible clicked queries

Interpretation:

- closer to 1.0 is better
- measures how far users have to dig before finding something worth opening

This is a behavioral metric, not a gold-label correctness metric.

### 4. No-click rate

Definition:

- share of eligible query intents with no click event inside a declared attribution window

Recommended v1 attribution window:

- 30 minutes from the terminal query in the burst

This metric is sensitive to logging coverage and product UX. It should be paired with query class and session context, not interpreted in isolation.

### 5. Per-intent retrieval accuracy

Definition:

- paper and section hit@k computed separately for each intent bucket

This is mandatory because aggregate scores can hide severe regressions in long-tail or recent-paper searches.

### 6. Retrieval latency

Definition:

- p50, p95, and p99 query latency

Measure separately for:

- paper search
- section search
- rerank off
- rerank on

Latency should be reported alongside accuracy, never instead of accuracy.

### Harness lift metrics

Harness metrics must be computed per harness and per fixed model configuration. Do not pool Codex and Claude Code into one number unless a hierarchical aggregation plan is explicitly declared.

### 1. Task success rate

Definition:

- share of tasks judged successfully completed

This is the primary user-outcome metric.

### 2. Fully grounded answer rate

Definition:

- share of answers where every material paper-dependent claim is supported by acceptable cited evidence

This is stricter than "has citations."

### 3. Unsupported claim rate

Definition:

- number of unsupported material claims divided by total material claims

This is a claim-level metric and should be reported even when answer-level task success is acceptable.

### 4. Evidence precision

Definition:

- share of cited evidence items that are directly relevant and supportive

If a system cites vaguely related papers or wrong sections, evidence precision drops even if the final answer sounds plausible.

### 5. Evidence recall

Definition:

- among tasks that require specific evidence, share where at least one acceptable evidence item is cited

Precision and recall should be reported together when possible.

### 6. Turns to success

Definition:

- number of user-visible turns until successful completion

For offline paired evaluations, emulate this through fixed follow-up budgets or staged tasks. For online product studies, use real session traces.

### 7. Cost metrics

Definition:

- latency
- tool calls
- token usage
- optional external API cost

All quality improvements should be interpreted together with cost.

## Experimental design for harness lift

### Offline paired A/B

For each task:

- run condition A without Paperbase
- run condition B with Paperbase
- keep model, prompt, and tool budget fixed except for Paperbase availability
- randomize condition order across tasks
- use fresh threads so the second condition cannot inherit hidden state from the first

### Repeated runs for stochastic systems

If the model is stochastic or does not guarantee seed determinism:

- run multiple repeats per task-condition
- pair repeats by seed when supported
- otherwise pair by run index and treat repeat as nested within task

Recommended:

- 3 repeats for representative gate studies
- 5 repeats for publishable studies on high-variance harnesses

### Online validation when traffic allows

Once product traffic is sufficient, validate offline findings online using:

- shadow evaluation
- randomized exposure
- or controlled canaries

Online studies should use the same pre-declared primary metrics where possible.

## Statistical analysis plan

### Confidence intervals

Default:

- 95% confidence intervals

Recommended estimators:

- stratified bootstrap for weighted benchmark panels
- clustered bootstrap by user for query studies when multiple queries come from the same user
- clustered bootstrap by task for harness studies with repeated runs

### Hypothesis tests

Suggested defaults:

- McNemar's test for paired binary task outcomes
- paired permutation or bootstrap tests for paired metric deltas
- nonparametric bootstrap for MRR and weighted hit@k differences

Hypothesis tests are secondary to effect sizes and confidence intervals.

### Multiple comparisons

Pre-declare:

- primary metrics
- secondary metrics
- exploratory subgroup analyses

Do not headline an exploratory subgroup win without labeling it as exploratory.

### Weighting

If rare strata are oversampled:

- retain inclusion probabilities
- compute weighted headline estimates
- publish unweighted counts alongside weighted results

### Equivalence and inconclusive rules

For each primary metric, define a minimally important effect size before the study.

Interpretation rules:

- confidence interval entirely above zero: evidence of improvement
- confidence interval entirely below zero: evidence of regression
- confidence interval overlaps zero and the minimally important effect size: inconclusive
- confidence interval lies fully inside pre-declared equivalence bounds: no practically meaningful difference

This rule exists specifically to prevent "one spoonful, no whale" conclusions.

## Bias and validity controls

### Sample bias

Risk:

- benchmark sample does not reflect the target population

Controls:

- stratified sampling
- weighted estimation
- sample-vs-population coverage table
- separate representative and stress panels

### Survivorship bias

Risk:

- only successfully indexed papers or successful user sessions are analyzed

Controls:

- include failures in telemetry
- report denominator definitions explicitly
- do not filter out dead-letter or timeout cases from reliability metrics

### Prompt or ranking overfit

Risk:

- benchmark items become known and tuned against

Controls:

- keep a fixed canary set, but refresh representative holdouts by time
- maintain hidden holdout subsets for release evaluation when feasible

### Label leakage

Risk:

- annotators see candidate rankings before defining acceptable answers

Controls:

- label from raw artifacts first
- if pooling system outputs for recall, pool multiple systems and adjudicate blind to condition

### Judge bias

Risk:

- raters know which answers used Paperbase

Controls:

- blind answers where practical
- strip obvious metadata from judging views
- adjudicate disagreements

### Temporal drift

Risk:

- query behavior or paper mix changes over time, making old benchmarks stale

Controls:

- time-stamped benchmark windows
- rolling refresh cadence
- quarterly or release-bound representative refresh

## Reporting rules

Every scorecard run should include:

- study tier
- time window
- target population
- inclusion and exclusion rules
- sample sizes by stratum
- model and harness versions
- metric definitions
- confidence intervals
- weighted and unweighted summaries where weighting is used

Public-facing claims should additionally include:

- whether the result is representative or stress-only
- whether the result is offline or online
- whether the result is confirmatory or exploratory

## Minimum claim thresholds

The following are recommended default guardrails.

### External or company-wide claims

Require all of:

- representative or publishable tier
- explicit uncertainty bars
- declared effect size threshold
- no major uncovered strata in the target population

### PR or release notes

Allow:

- canary and representative gate results

Require:

- explicit labeling of benchmark tier
- no pooling of canary and representative results into one number

### Null claims

Require:

- power or equivalence justification

Otherwise the correct label is:

- `inconclusive`

## Practical v1 recommendations

1. Keep the current 10-paper audit, but rename it mentally as a canary, not a representative study.
2. Build the real-user-query benchmark in #21 as the first representative retrieval gate.
3. Add a representative indexing audit with weighted human review, not just telemetry.
4. Build harness A/B evaluations as paired studies with fixed-model conditions and repeated runs.
5. Do not publish one combined Paperbase score. Publish layered results with uncertainty.

## Out of scope

This document defines how to measure the scorecard scientifically. It does not choose:

- the final CLI surface
- the final dashboard implementation
- the final rubric wording for each annotation form

Those should follow this spec, not replace it.
