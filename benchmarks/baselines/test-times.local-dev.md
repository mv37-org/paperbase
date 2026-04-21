# Test Timing Report

- Generated: `2026-04-21T23:24:07Z`
- Git SHA: `f97e456`

## Suite Totals

| Suite | Tests | Total |
| --- | ---: | ---: |
| backend | 86 | 4.481s |
| plugin | 4 | 0.025s |
| chat | 8 | 0.068s |

## Slowest Tests

| Test | Seconds |
| --- | ---: |
| `backend::tests.test_service::test_index_writes_manifest_and_artifacts` | 3.243s |
| `backend::tests.test_service::test_browser_uploads_and_chat_require_api_key_after_free_quota` | 0.121s |
| `backend::tests.test_integration_local_profile::test_sql_repository_search_and_result_set_flow` | 0.120s |
| `backend::tests.test_service::test_browser_auth_and_password_reset_flow` | 0.084s |
| `backend::tests.test_service::test_link_requests_force_tool_use_and_prompt_for_paper_links` | 0.084s |
| `backend::tests.test_service::test_search_and_grep_return_result_handles` | 0.076s |
| `backend::tests.test_service::test_browser_chat_stream_marks_partial_failure` | 0.074s |
| `backend::tests.test_service::test_search_can_shortlist_from_generated_summaries` | 0.072s |
| `backend::tests.test_integration_local_profile::test_probe_integrations_against_active_local_profile` | 0.070s |
| `backend::tests.test_service::test_reindex_replaces_stale_artifacts` | 0.057s |
| `backend::tests.test_service::test_browser_chat_stream_emits_citations_and_completion` | 0.057s |
| `backend::tests.test_service::test_api_flow_for_jobs_and_results` | 0.056s |
| `backend::tests.test_service::test_search_only_tool_results_do_not_create_citations` | 0.056s |
| `backend::tests.test_integration_local_profile::test_worker_processes_summarize_job_with_real_repository` | 0.049s |
| `backend::tests.test_service::test_browser_paper_read_endpoints` | 0.048s |

## Slower Than Baseline

| Test | Current | Baseline | Delta |
| --- | ---: | ---: | ---: |
| `backend::tests.test_service::test_browser_uploads_and_chat_require_api_key_after_free_quota` | 0.121s | 0.108s | 0.013s |
| `backend::tests.test_integration_local_profile::test_probe_integrations_against_active_local_profile` | 0.070s | 0.059s | 0.011s |
| `backend::tests.test_service::test_link_requests_force_tool_use_and_prompt_for_paper_links` | 0.084s | 0.076s | 0.008s |
| `backend::tests.test_service::test_browser_chat_stream_handles_api_key_required_without_placeholder` | 0.038s | 0.031s | 0.007s |
| `backend::tests.test_service::test_search_can_shortlist_from_generated_summaries` | 0.072s | 0.065s | 0.007s |
| `backend::tests.test_service::test_search_only_tool_results_do_not_create_citations` | 0.056s | 0.051s | 0.005s |
| `plugin::tests.test_plugin::test_manifests_reference_shared_mcp_config` | 0.020s | 0.019s | 0.001s |
| `backend::tests.test_api_auth::test_bearer_routes_delegate_to_service` | 0.014s | 0.013s | 0.001s |
| `backend::tests.test_dev_cli::test_main_dispatches_profile_with_current_settings` | 0.002s | 0.001s | 0.001s |
| `backend::tests.test_service::test_probe_integrations_returns_database_and_storage_status` | 0.002s | 0.001s | 0.001s |
| `backend::tests.test_service::test_reindex_continues_past_failed_frontier_page` | 0.002s | 0.001s | 0.001s |
| `backend::tests.test_api_auth::test_parse_tags_normalizes_commas_and_newlines` | 0.018s | 0.017s | 0.001s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App falls back to the non-streaming send path when the stream fails before any events` | 0.007s | 0.007s | 0.000s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/api.test.ts::chat api marks pre-event stream failures as fallback-safe` | 0.001s | 0.001s | 0.000s |
| `chat::/Users/arslnb/research/paper-ai/chat/src/App.test.tsx::App opens the cited-paper drawer from the URL state` | 0.011s | 0.011s | 0.000s |
