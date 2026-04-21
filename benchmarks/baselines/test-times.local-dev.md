# Test Timing Report

- Generated: `2026-04-21T23:16:29Z`
- Git SHA: `1dd41d5`

## Suite Totals

| Suite | Tests | Total |
| --- | ---: | ---: |
| backend | 81 | 4.510s |
| plugin | 4 | 0.024s |
| chat | 8 | 0.072s |

## Slowest Tests

| Test | Seconds |
| --- | ---: |
| `backend::tests.test_service::test_index_writes_manifest_and_artifacts` | 3.274s |
| `backend::tests.test_integration_local_profile::test_sql_repository_search_and_result_set_flow` | 0.132s |
| `backend::tests.test_service::test_browser_uploads_and_chat_require_api_key_after_free_quota` | 0.108s |
| `backend::tests.test_service::test_browser_auth_and_password_reset_flow` | 0.085s |
| `backend::tests.test_service::test_browser_chat_stream_marks_partial_failure` | 0.084s |
| `backend::tests.test_service::test_search_and_grep_return_result_handles` | 0.083s |
| `backend::tests.test_service::test_link_requests_force_tool_use_and_prompt_for_paper_links` | 0.076s |
| `backend::tests.test_service::test_search_can_shortlist_from_generated_summaries` | 0.065s |
| `backend::tests.test_service::test_reindex_replaces_stale_artifacts` | 0.064s |
| `backend::tests.test_service::test_browser_chat_stream_emits_citations_and_completion` | 0.061s |
| `backend::tests.test_integration_local_profile::test_probe_integrations_against_active_local_profile` | 0.059s |
| `backend::tests.test_service::test_api_flow_for_jobs_and_results` | 0.057s |
| `backend::tests.test_service::test_browser_paper_read_endpoints` | 0.053s |
| `backend::tests.test_integration_local_profile::test_worker_processes_summarize_job_with_real_repository` | 0.052s |
| `backend::tests.test_service::test_search_only_tool_results_do_not_create_citations` | 0.051s |
