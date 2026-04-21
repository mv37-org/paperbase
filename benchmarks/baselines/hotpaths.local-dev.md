# Hotpath Profile Report

- Generated: `2026-04-21T23:23:59Z`
- Git SHA: `f97e456`

## Scenario Wall Times

| Scenario | Wall Time |
| --- | ---: |
| index_single_paper | 0.001s |
| search_local_only | 0.000s |
| search_sections | 0.009s |
| reindex_missing_embeddings | 0.000s |
| chat_grounded_stream | 0.000s |
| worker_idle_iteration | 0.000s |
| worker_process_job | 0.000s |

## index_single_paper

| Function | Cumulative | Self Time |
| --- | ---: | ---: |
| `<lambda>` | 0.001s | 0.000s |
| `index_arxiv_paper` | 0.001s | 0.000s |
| `_vector` | 0.000s | 0.000s |
| `encode_batch` | 0.000s | 0.000s |
| `encode_paper` | 0.000s | 0.000s |
| `shorten` | 0.000s | 0.000s |
| `dumps` | 0.000s | 0.000s |
| `upsert_paper` | 0.000s | 0.000s |
| `__setattr__` | 0.000s | 0.000s |
| `fill` | 0.000s | 0.000s |

## search_local_only

| Function | Cumulative | Self Time |
| --- | ---: | ---: |
| `<lambda>` | 0.000s | 0.000s |
| `search` | 0.000s | 0.000s |
| `search_local` | 0.000s | 0.000s |
| `encode` | 0.000s | 0.000s |
| `_vector` | 0.000s | 0.000s |
| `__init__` | 0.000s | 0.000s |
| `<method 'validate_python' of 'pydantic_core._pydantic_core.SchemaValidator' objects>` | 0.000s | 0.000s |
| `score` | 0.000s | 0.000s |
| `save_result_set` | 0.000s | 0.000s |
| `best_chunk_for_papers` | 0.000s | 0.000s |

## search_sections

| Function | Cumulative | Self Time |
| --- | ---: | ---: |
| `<lambda>` | 0.009s | 0.000s |
| `search_sections` | 0.009s | 0.000s |
| `search_sections` | 0.008s | 0.000s |
| `execute` | 0.008s | 0.000s |
| `_execute_internal` | 0.008s | 0.000s |
| `execute` | 0.007s | 0.000s |
| `_execute_on_connection` | 0.007s | 0.000s |
| `_execute_clauseelement` | 0.007s | 0.000s |
| `wait` | 0.007s | 0.007s |
| `_execute_context` | 0.007s | 0.000s |

## reindex_missing_embeddings

| Function | Cumulative | Self Time |
| --- | ---: | ---: |
| `<lambda>` | 0.000s | 0.000s |
| `reindex_missing_embeddings` | 0.000s | 0.000s |
| `_vector` | 0.000s | 0.000s |
| `encode_batch` | 0.000s | 0.000s |
| `encode_paper_batch` | 0.000s | 0.000s |
| `list_papers_needing_embedding_backfill` | 0.000s | 0.000s |
| `<method 'disable' of '_lsprof.Profiler' objects>` | 0.000s | 0.000s |
| `needs_backfill` | 0.000s | 0.000s |
| `<method 'sort' of 'list' objects>` | 0.000s | 0.000s |
| `<lambda>` | 0.000s | 0.000s |

## chat_grounded_stream

| Function | Cumulative | Self Time |
| --- | ---: | ---: |
| `<lambda>` | 0.000s | 0.000s |
| `stream_chat_message` | 0.000s | 0.000s |
| `_prepare_chat_generation` | 0.000s | 0.000s |
| `add_chat_message` | 0.000s | 0.000s |
| `model_copy` | 0.000s | 0.000s |
| `model_dump` | 0.000s | 0.000s |
| `<method 'to_python' of 'pydantic_core._pydantic_core.SchemaSerializer' objects>` | 0.000s | 0.000s |
| `new_id` | 0.000s | 0.000s |
| `uuid4` | 0.000s | 0.000s |
| `__copy__` | 0.000s | 0.000s |

## worker_idle_iteration

| Function | Cumulative | Self Time |
| --- | ---: | ---: |
| `<lambda>` | 0.000s | 0.000s |
| `run_one_iteration` | 0.000s | 0.000s |
| `model_copy` | 0.000s | 0.000s |
| `__copy__` | 0.000s | 0.000s |
| `<method 'disable' of '_lsprof.Profiler' objects>` | 0.000s | 0.000s |
| `debug` | 0.000s | 0.000s |
| `copy` | 0.000s | 0.000s |
| `cleanup_expired_results` | 0.000s | 0.000s |
| `isEnabledFor` | 0.000s | 0.000s |
| `cleanup_expired_results` | 0.000s | 0.000s |

## worker_process_job

| Function | Cumulative | Self Time |
| --- | ---: | ---: |
| `process_next_job` | 0.000s | 0.000s |
| `index_arxiv_paper` | 0.000s | 0.000s |
| `_vector` | 0.000s | 0.000s |
| `encode_batch` | 0.000s | 0.000s |
| `encode_paper` | 0.000s | 0.000s |
| `upsert_paper` | 0.000s | 0.000s |
| `model_copy` | 0.000s | 0.000s |
| `dumps` | 0.000s | 0.000s |
| `replace_text_chunks` | 0.000s | 0.000s |
| `encode` | 0.000s | 0.000s |

## SQL Plans

| Query | Execution | Planning |
| --- | ---: | ---: |
| search_local.lexical | 46.16ms | 1.59ms |
| search_local.vector | 0.02ms | 0.06ms |
| search_sections.lexical | 0.11ms | 0.36ms |
| search_sections.vector | 0.05ms | 0.03ms |
| suggest_papers | 0.09ms | 0.13ms |
| best_chunk_for_papers | 0.07ms | 0.04ms |
| grep_result_set | 0.05ms | 0.61ms |
| claim_pending_job | 0.01ms | 0.17ms |

## Scenario Deltas

| Scenario | Current | Baseline | Delta |
| --- | ---: | ---: | ---: |
| search_sections | 0.009s | 0.007s | 0.001s |
| search_local_only | 0.000s | 0.000s | 0.000s |
| worker_idle_iteration | 0.000s | 0.000s | -0.000s |
| chat_grounded_stream | 0.000s | 0.000s | -0.000s |
| reindex_missing_embeddings | 0.000s | 0.000s | -0.000s |
| worker_process_job | 0.000s | 0.000s | -0.000s |
| index_single_paper | 0.001s | 0.001s | -0.000s |
