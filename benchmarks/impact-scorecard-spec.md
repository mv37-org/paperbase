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

## Benchmark suite

This scorecard should be implemented as a series of benchmarks, not one monolithic run.

Each benchmark must declare:

- benchmark id
- benchmark tier
- target population
- primary metrics
- whether the benchmark is hardware-sensitive
- whether the benchmark is expected to be representative, canary-only, or stress-only

### Benchmark catalogue

| ID | Benchmark | Goal | Primary outputs | Representative? | Hardware-sensitive? |
| --- | --- | --- | --- | --- | --- |
| `IQ-1` | Indexing census | Measure corpus-wide indexing coverage and quality telemetry | `source_type`, artifact coverage, embedding coverage, freshness, reliability | yes | low |
| `IQ-2` | Stratified extraction audit | Human-audit extraction fidelity on a representative sample | rubric scores, answerability, weighted pass rates | yes | low |
| `IQ-3` | Indexing stress audit | Audit rare or fragile extraction paths | same as `IQ-2`, stratified to hard cases | no, stress-only | low |
| `RQ-1` | Paper retrieval canary | Fast regression detection on fixed paper-search queries | hit@1/3/5/10 | no, canary-only | low |
| `RQ-2` | Representative real-query paper retrieval | Estimate real paper-search quality | weighted paper hit@k, MRR, no-click rate | yes | low |
| `RQ-3` | Representative section retrieval | Estimate passage/evidence retrieval quality | weighted section hit@k, evidence recall | yes | low |
| `RQ-4` | Retrieval stress suite | Exercise difficult but important query classes | per-class hit@k on typo, acronym, long-tail, figure/table, code cases | no, stress-only | low |
| `HL-1` | Harness A/B representative task panel | Estimate end-to-end task lift with vs without Paperbase | task success, grounding, unsupported claims, cost | yes | medium |
| `HL-2` | Harness A/B stress panel | Measure lift on rare but strategically important tasks | same as `HL-1` on figure/table/code/comparison tasks | no, stress-only | medium |
| `PF-1` | Deterministic hotpath profile | Track backend hotpath latency drift | wall-clock hotpaths, SQL plan deltas | no, engineering-only | high |
| `PF-2` | End-to-end cost and latency profile | Track user-facing cost under fixed harness settings | latency, tool calls, tokens, throughput | no unless hardware is fixed | high |

### Current and target execution surfaces

Current repo-supported entrypoints:

- `make doctor`
- `make test`
- `make profile`
- `make benchmark`
- `.venv/bin/paperbase-dev benchmark iq-1`

Today, the deterministic performance profile is fully implemented as a stable repo command via `make profile`, and the first scorecard benchmark slice is available through both `make benchmark BENCHMARK=iq-1` and `paperbase-dev benchmark run iq-1`.

This spec requires the future benchmark surface to live behind the supported repo workflow, preferably through `paperbase-dev` and exposed at the repo root through `make` targets, rather than through ad hoc shell scripts.

Until dedicated runners exist, benchmark studies should still follow the execution controls in this document and store versioned outputs under `benchmarks/`.

## Execution controls

These controls are mandatory. The goal is to keep benchmark outcomes from being dominated by:

- paper selection artifacts
- machine differences
- external API drift
- cache luck
- query leakage
- judge leakage

### Paper neutrality controls

Paper-specific skew is one of the easiest ways to fool ourselves. A benchmark that over-indexes on famous, highly cited, or already hand-curated papers can overstate quality badly.

Mandatory controls:

- cap the number of benchmark items contributed by any one paper on representative panels
- keep train, development, and holdout splits paper-disjoint where feasible
- report both weighted population averages and paper-macro averages when a benchmark contains multiple items per paper
- cluster confidence intervals by paper for paper-derived tasks and queries
- stratify by paper attributes that can distort results:
  - source type
  - primary category family
  - publication year bucket
  - citation bucket
  - artifact richness bucket
- blind human raters to title, authors, venue, and citation count when those fields are not needed for the judgment

Recommended v1 paper caps:

- representative retrieval panels: no more than 3 labeled queries per paper
- representative harness panels: no more than 2 tasks per paper unless the task family explicitly requires multiple evidence modes from the same paper
- canary and stress panels may exceed these caps, but must be labeled as such

### Hardware neutrality controls

Hardware neutrality does not mean pretending hardware does not matter. It means separating:

- content-quality benchmarks, which should be nearly hardware-agnostic
- performance benchmarks, which are inherently hardware-sensitive

Mandatory controls:

- never pool latency numbers across materially different hardware classes into one headline metric
- for paired A/B studies, run both conditions on the same host class and software image
- record a hardware fingerprint for every run:
  - CPU model
  - GPU model if present
  - RAM
  - operating system
  - Python version
  - database version
  - embedding and reranker model ids
- report wall-clock performance only within fixed hardware tracks
- prefer relative deltas over cross-machine absolute comparisons
- for quality benchmarks, treat wall-clock latency as a secondary metric, not the primary outcome

Recommended hardware tracks:

- `local-cpu`
- `local-gpu`
- `cloud-cpu`
- `cloud-gpu`

Do not compare a `local-cpu` absolute p95 latency to a `cloud-gpu` absolute p95 latency and call that a product result.

### External source neutrality controls

External APIs change. Live search backends change. Enrichment changes. Those sources are useful, but they are hostile to reproducibility.

Mandatory controls for primary offline quality benchmarks:

- freeze the indexed corpus snapshot
- set `include_live_sources=False`
- freeze model ids
- freeze the benchmark item list version
- freeze the labels version

If a benchmark explicitly measures live-source behavior, it must be reported as a separate benchmark with:

- run timestamp
- external providers used
- exact request settings

Live-source runs are product telemetry or exploratory studies unless repeated over multiple windows with explicit controls.

### Order, cache, and warmup controls

Mandatory controls:

- randomize benchmark item order within each run where order effects are plausible
- for paired A/B benchmarks, randomize which condition runs first per item
- use fresh namespaces or fresh threads so state does not leak across conditions
- declare one cache policy and stick to it for the benchmark:
  - cold-cache
  - warm-cache after fixed warmup
- if warm-cache is used, warm both conditions symmetrically

Recommended default:

- quality benchmarks: warm model weights, fresh task/thread state
- performance benchmarks: publish both cold and warm numbers when feasible, but keep one as the primary track

### Repetition and replication controls

Mandatory controls:

- repeat stochastic harness runs
- rerun performance benchmarks multiple times
- use time-separated study windows for publishable representative studies

Recommended defaults:

- retrieval quality on a frozen corpus: one retrieval pass is acceptable if the system is deterministic
- harness quality on stochastic models: at least 3 repeats per task-condition for representative gate studies, 5 for publishable studies
- hotpath performance: at least 5 full benchmark runs on the same hardware track before updating baselines

### Contamination controls

Mandatory controls:

- benchmark items sampled from real user traffic must exclude synthetic tool reformulations
- benchmark items used as canaries should not be the only items used during development decisions
- labels must not be authored from a single system's ranking alone
- if prompts or retrieval heuristics are tuned on one panel, the final claim must use a paper-disjoint or time-disjoint holdout

## How to run each benchmark

These runbooks define the benchmark procedure. Where the repo already has a stable command, use it. Where a dedicated runner does not yet exist, treat this as the required protocol for the future `paperbase-dev` implementation.

### `IQ-1`: Indexing census

Purpose:

- measure corpus-wide indexing telemetry without hand-label bias

How to run:

1. Declare the study window and target population.
2. Snapshot all papers indexed in that window.
3. Export paper-level fields needed for measurement:
   - `source_type`
   - indexing timestamps
   - embedding presence
   - artifact counts from manifests and chunks
4. Compute all metrics over the full snapshot, not a sample.
5. Report by relevant strata:
   - source type
   - category family
   - publication year bucket
   - ingest mode if available

Bias controls:

- this is a census, so the main risks are instrumentation and survivorship bias, not sampling variance
- failed and partial index attempts must remain in reliability denominators
- do not filter away `abstract-only` or failed cases because they are inconvenient

Hardware controls:

- none for the primary metrics beyond provenance capture, because the outputs are telemetry summaries

Primary use:

- representative system-state reporting

### `IQ-2`: Stratified extraction audit

Purpose:

- judge whether indexed artifacts are actually readable and useful

How to run:

1. Draw a stratified sample from indexed papers in the study window.
2. Oversample fragile strata such as `pdf` and `abstract-only`.
3. Create an audit packet per paper containing only the artifact views needed for judgment.
4. Blind raters to title, authors, venue, and citation count where feasible.
5. Collect independent ratings on the frozen rubric.
6. Adjudicate disagreements.
7. Weight results back to the indexed-paper population.

Bias controls:

- stratified sampling with inclusion probabilities
- paper caps do not apply because the unit is paper itself
- raters must not know whether a paper is a famous landmark unless that information is unavoidable

Hardware controls:

- none for the primary metrics beyond provenance capture

Primary use:

- representative extraction fidelity estimate

### `IQ-3`: Indexing stress audit

Purpose:

- stress-test rare extraction paths that are too sparse to dominate representative panels

How to run:

1. Construct a fixed panel of hard papers:
   - scanned PDFs
   - figure-heavy papers
   - table-heavy papers
   - LaTeX include trees
   - papers that previously failed extraction
2. Reindex them under controlled conditions.
3. Run the same rubric as `IQ-2`.
4. Report separately from representative estimates.

Bias controls:

- do not fold stress-panel results into representative headline metrics

Hardware controls:

- same as `IQ-2`

Primary use:

- failure discovery and regression diagnosis

### `RQ-1`: Paper retrieval canary

Purpose:

- catch obvious paper-ranking regressions quickly

How to run:

1. Use a fixed, versioned query set.
2. Use a frozen corpus snapshot.
3. Disable live sources.
4. Run retrieval deterministically and compute hit@k.

Bias controls:

- canary set must not be the sole basis for product claims
- cap overrepresented landmark papers where possible

Hardware controls:

- quality outputs are primary; latency is secondary

Primary use:

- PR and local regression detection

### `RQ-2`: Representative real-query paper retrieval

Purpose:

- estimate real paper-search quality on the actual user-query distribution

How to run:

1. Select a time window of eligible explicit user searches.
2. Collapse bursts into canonical query intents.
3. Draw a stratified sample from those canonical intents.
4. Label acceptable papers without exposing a single candidate system's ranking as the gold source.
5. Evaluate retrieval on a frozen corpus snapshot with live sources disabled.
6. Compute weighted hit@k and behavioral metrics separately.

Bias controls:

- sample from all eligible queries, not just clicked queries
- cap the number of sampled queries per paper
- cluster uncertainty by paper and by user when appropriate
- keep the final evaluation window time-disjoint from tuning data

Hardware controls:

- quality metrics are primary
- if latency is reported, it must come from a fixed hardware track

Primary use:

- representative retrieval gate

### `RQ-3`: Representative section retrieval

Purpose:

- measure whether the system finds the right evidence passage, not just the right paper

How to run:

1. Start from evidence-seeking queries or task prompts.
2. Label acceptable evidence paths or spans.
3. Evaluate section search on a frozen corpus snapshot.
4. Compute weighted section hit@k and evidence recall.

Bias controls:

- cap overrepresented papers
- allow multiple acceptable evidence locations when the paper contains repeated or equivalent support
- use pooled candidate systems if retrieval outputs help annotation

Hardware controls:

- same as `RQ-2`

Primary use:

- representative evidence retrieval estimate

### `RQ-4`: Retrieval stress suite

Purpose:

- characterize known weak spots that may not be frequent enough to dominate representative traffic

How to run:

1. Build fixed hard subsets:
   - typo-heavy
   - acronym-only
   - long pasted text
   - long-tail author queries
   - code-linked paper queries
   - figure/table requests
2. Evaluate with the same frozen corpus and label policy as the representative suites.
3. Report per-subset metrics only.

Bias controls:

- never weight this stress panel into representative product averages unless the weighting scheme is explicitly justified

Hardware controls:

- same as `RQ-2`

Primary use:

- stress diagnosis and targeted improvements

### `HL-1`: Harness A/B representative task panel

Purpose:

- estimate end-to-end task lift with and without Paperbase for a fixed harness and model

How to run:

1. Sample realistic tasks from the target harness population.
2. Keep the model, prompt family, and non-Paperbase tool surface fixed.
3. Run paired conditions:
   - without Paperbase
   - with Paperbase
4. Randomize condition order per task.
5. Use fresh threads or fresh state for each condition.
6. Repeat runs when the model is stochastic.
7. Blind judges to condition during scoring.

Bias controls:

- representative task sampling
- cap tasks per paper
- keep paper-disjoint holdouts if the task bank is reused during development
- separate task success from grounding quality during scoring

Hardware controls:

- run both A and B on the same hardware track
- report quality as primary and latency/cost as secondary
- do not pool runs from materially different hardware classes into one latency claim

Primary use:

- representative harness lift estimate

### `HL-2`: Harness A/B stress panel

Purpose:

- estimate Paperbase lift on rare but strategically important task families

How to run:

1. Build fixed stress tasks for:
   - figure interpretation
   - table extraction
   - code-linked paper analysis
   - cross-paper comparison
   - adversarial ambiguity
2. Run the same paired design as `HL-1`.
3. Report separately from representative task panels.

Bias controls:

- no representative claims from this panel alone

Hardware controls:

- same-host paired runs

Primary use:

- high-value failure discovery

### `PF-1`: Deterministic hotpath profile

Purpose:

- catch performance regressions in indexing and retrieval hotpaths

How to run:

1. Use the supported repo workflow:
   - `make doctor`
   - `make profile`
2. Run on a quiet machine in a declared hardware track.
3. Rerun at least 5 times before accepting a new baseline update for publishable trend claims.
4. Report median and spread, not only one run.

Bias controls:

- none related to paper representativeness, because this is an engineering performance benchmark, not a product outcome benchmark
- do not use `PF-1` to argue that retrieval quality improved

Hardware controls:

- strict same-hardware comparison
- capture hardware fingerprint and database versions
- compare deltas within track only

Primary use:

- engineering performance regression tracking

### `PF-2`: End-to-end cost and latency profile

Purpose:

- measure user-visible cost under controlled harness settings

How to run:

1. Fix harness, model, prompt family, and benchmark task set.
2. Run all conditions on the same hardware track.
3. Capture:
   - total latency
   - time to first meaningful output when available
   - tool calls
   - token usage
4. Repeat runs and report distributions.

Bias controls:

- do not confuse this with a representative quality benchmark
- if the task set is a canary or stress panel, label it as such

Hardware controls:

- strict same-host or same-class comparison
- no cross-hardware absolute claims

Primary use:

- cost-quality tradeoff analysis

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
