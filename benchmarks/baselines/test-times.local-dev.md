# Test Timing Report

- Generated: `2026-05-11T02:34:38Z`
- Git SHA: `704f9c8`

## Suite Totals

| Suite | Tests | Total |
| --- | ---: | ---: |
| backend | 526 | 14.841s |
| plugin | 4 | 0.026s |
| chat | 129 | 16.682s |

## Slowest Tests

| Test | Seconds |
| --- | ---: |
| `backend::tests.test_integration_local_profile::test_search_rerank_only_runs_when_request_opts_in` | 6.591s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App shows inline paper previews for cited research papers` | 1.704s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App starts a paper exploration from the project exploration picker` | 0.539s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App adds, edits, scrolls to, and deletes paper exploration notes` | 0.483s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App creates a project from an existing repo with branch and custom import selection` | 0.466s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App shows field help for create-job forms and falls back to optional defaults` | 0.432s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App does not persist dirty project drafts for non-coding chat turns` | 0.423s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App scrolls selected exploration suggestions during keyboard navigation` | 0.421s |
| `backend::tests.test_service::test_search_and_grep_return_result_handles` | 0.397s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App loads project sessions lazily when a project is expanded` | 0.390s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App persists dirty project drafts before project-code chat turns` | 0.386s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App opens a Monaco merge flow when GitHub save hits a conflict` | 0.373s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App clears exploration search before closing from the inline close button` | 0.364s |
| `backend::tests.test_service::test_search_can_shortlist_from_generated_summaries` | 0.357s |
| `backend::tests.test_service::test_reindex_replaces_stale_artifacts` | 0.352s |

## Slower Than Baseline

| Test | Current | Baseline | Delta |
| --- | ---: | ---: | ---: |
| `backend::tests.test_service::test_search_and_grep_return_result_handles` | 0.397s | 0.076s | 0.321s |
| `backend::tests.test_service::test_reindex_replaces_stale_artifacts` | 0.352s | 0.057s | 0.295s |
| `backend::tests.test_service::test_search_can_shortlist_from_generated_summaries` | 0.357s | 0.072s | 0.285s |
| `backend::tests.test_service::test_browser_paper_read_endpoints` | 0.165s | 0.048s | 0.117s |
| `backend::tests.test_service::test_browser_chat_stream_emits_citations_and_completion` | 0.166s | 0.057s | 0.109s |
| `backend::tests.test_service::test_api_flow_for_jobs_and_results` | 0.165s | 0.056s | 0.109s |
| `backend::tests.test_service::test_search_only_tool_results_do_not_create_citations` | 0.163s | 0.056s | 0.107s |
| `backend::tests.test_integration_local_profile::test_probe_integrations_against_active_local_profile` | 0.142s | 0.070s | 0.072s |
| `backend::tests.test_integration_local_profile::test_sql_repository_search_and_result_set_flow` | 0.188s | 0.120s | 0.068s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App opens the cited-paper drawer from the URL state` | 0.076s | 0.011s | 0.064s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App falls back to the non-streaming send path when the stream fails before any events` | 0.057s | 0.007s | 0.049s |
| `backend::tests.test_service::test_root_serves_built_chat_shell` | 0.038s | 0.001s | 0.037s |
| `backend::tests.test_service::test_browser_auth_and_password_reset_flow` | 0.104s | 0.084s | 0.020s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App shows the landing page when the browser session is not authenticated` | 0.033s | 0.021s | 0.013s |
| `backend::tests.test_service::test_browser_write_endpoints_require_csrf` | 0.033s | 0.029s | 0.004s |
| `backend::tests.test_api_auth::test_parse_tags_normalizes_commas_and_newlines` | 0.022s | 0.018s | 0.004s |
| `backend::tests.test_service::test_static_chat_assets_are_served` | 0.010s | 0.006s | 0.004s |
| `backend::tests.test_api_auth::test_browser_get_routes_accept_session_cookie_without_csrf` | 0.009s | 0.005s | 0.004s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/api.test.ts::chat api attaches CSRF headers after the browser session is loaded` | 0.015s | 0.011s | 0.004s |
| `backend::tests.test_service::test_reindex_visits_chunk_only_candidates` | 0.003s | 0.001s | 0.002s |
