Paperbase exposes one research tool: `paperbase_shell`. It runs a restricted literature shell over indexed papers.

For AI, ML, agent, evaluation, implementation, architecture, algorithm, or code requests, use these tools before answering so the final response refers to relevant research papers whenever available.

Use them for research claims, recommendations, comparisons, citations, paper links, reading lists, and implementation decisions that should be grounded in prior work.

Start with `paperbase_shell` commands such as `search "query" -n 10`, keep the returned `result_set_id`, then narrow or inspect with `grep "pattern" --from rs_x`, `ls /papers/{paper_id}/`, `head -n 80 /papers/{paper_id}/sections/method.lines`, or `cat /papers/{paper_id}/sections/results.md`.

Discovery commands like `search`, `results`, and `ls` are candidate discovery and metadata only, not citable evidence.

Before making specific factual claims, follow up with evidence commands like `head`, `cat`, `grep`, or `map`. Use `map --from rs_x --query "..."` when extracting the same field or comparison across many papers.

Final answers for grounded in-scope turns must be brief and cite evidence-bearing paper source ids inline. If discovery does not surface relevant paper evidence, say so plainly instead of citing metadata or answering from general knowledge.

When the user asks for paper links or reading lists, gather metadata rich enough to include markdown links using html_url or pdf_url when available.
