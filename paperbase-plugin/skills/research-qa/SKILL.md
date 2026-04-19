---
name: research-qa
description: Answer broad AI research questions from indexed papers by using research_qa first, then grounding the response in the returned paper shortlist and section-level evidence.
---

# Research-QA Workflow

Use this workflow when the user asks an open-ended research question such as how to build an agent, what the latest methods are, or how approaches compare across papers.

1. Call `research_qa` first.
2. Treat the returned `shortlisted_papers`, `relevant_sections`, and `evidence` as the working set for the answer.
3. Write the answer from the indexed evidence, not from base-model memory.
4. Cite papers and section paths when making specific claims.
5. If the question needs deeper proof, follow up with `read_paper_file`, `get_table`, or `get_figure` on the strongest hits.
6. If the corpus is thin or the evidence is weak, say that clearly and suggest indexing more papers or widening the query.
