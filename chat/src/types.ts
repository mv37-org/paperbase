export type AuthView = "login" | "signup" | "reset-request" | "reset-password" | "settings";

export interface BrowserUser {
  user_id: string;
  email: string;
  display_name: string;
  is_admin?: boolean;
}

export interface BrowserUserSettingsResponse {
  openai_api_key_hint: string;
  anthropic_api_key_hint?: string;
  has_modal_credentials: boolean;
  modal_token_id_hint: string;
  has_github_connection: boolean;
  github_login: string;
  github_oauth_available: boolean;
  github_manage_access_url: string;
  weekly_papers_review_email: boolean;
  appearance_theme: "light" | "dark";
}

export interface BrowserSessionResponse {
  authenticated: boolean;
  user: BrowserUser | null;
  has_openai_api_key: boolean;
  has_anthropic_api_key?: boolean;
  settings?: BrowserUserSettingsResponse;
  csrf_token: string | null;
}

export interface PaperResponse {
  paper_id: string;
  arxiv_id: string;
  title: string;
  authors: string;
  abstract: string;
  summary_short: string;
  summary_eli5: string;
  categories: string;
  primary_category: string;
  published: string;
  updated: string;
  doi?: string | null;
  comment?: string | null;
  journal_ref?: string | null;
  pdf_url: string;
  html_url: string;
  citation_count: number;
  influential_citation_count: number;
  semantic_scholar_id?: string | null;
  code_url?: string | null;
  source_type?: string | null;
  index_status: string;
  indexed_at?: string | null;
}

export interface JobResponse {
  job_id: string;
  root_job_id?: string | null;
  job_type: string;
  status: string;
  payload: Record<string, unknown>;
  error?: string | null;
  attempts: number;
  max_attempts: number;
  next_run_at?: string | null;
  last_attempted_at?: string | null;
  priority: number;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface AdminWorkerSummary {
  status: string;
  stalled: boolean;
  reachable: boolean;
  source: string;
  last_loop_at?: string | null;
  retry_scheduled_count: number;
  dead_count: number;
  last_error?: string | null;
  last_terminal_error?: string | null;
}

export interface AdminJobOverviewRow {
  job_id: string;
  root_job_id: string;
  job_type: string;
  status: string;
  display_status: string;
  payload: Record<string, unknown>;
  error?: string | null;
  attempts: number;
  max_attempts: number;
  priority: number;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  is_synthetic: boolean;
  tracking_available: boolean;
  indexed_paper_count?: number | null;
  paper_outcome_counts?: Record<string, number> | null;
  latest_status_summary: string;
  child_job_count: number;
  details: Record<string, unknown>;
}

export interface AdminJobsOverviewResponse {
  jobs: AdminJobOverviewRow[];
  worker?: AdminWorkerSummary | null;
}

export interface AdminJobPaperResponse {
  root_job_id: string;
  job_id: string;
  paper_id: string;
  arxiv_id?: string | null;
  title?: string | null;
  outcome: string;
  skip_reason?: string | null;
  created_at: string;
  updated_at: string;
  paper?: PaperResponse | null;
}

export interface AdminJobPapersResponse {
  root_job_id: string;
  total_count: number;
  offset: number;
  limit: number;
  has_more: boolean;
  outcome_counts?: Record<string, number>;
  skipped_reason_counts?: Record<string, number>;
  papers: AdminJobPaperResponse[];
}

export interface FileContentResponse {
  paper_id: string;
  path: string;
  content: string;
}

export interface FileObject {
  path: string;
  size: number;
  content_type?: string | null;
}

export interface FileListResponse {
  paper_id: string;
  files: FileObject[];
}

export interface PaperAssetResponse {
  paper_id: string;
  path: string;
  url: string;
  content_type?: string | null;
}

export interface PaperPreviewResponse {
  paper_id: string;
  image_url: string;
  content_type: string;
}

export interface FigureResponse {
  paper_id: string;
  figure_id: string;
  caption: string;
  image_url?: string | null;
  content_type?: string | null;
}

export interface TableResponse {
  paper_id: string;
  table_id: string;
  caption?: string | null;
  rows: string[][];
  csv_url?: string | null;
}

export interface PaperVoteSummary {
  paper_id: string;
  upvote_count: number;
  viewer_has_upvoted: boolean;
}

export interface PaperCommentAuthor {
  user_id: string;
  display_name: string;
}

export interface PaperComment {
  comment_id: string;
  paper_id: string;
  parent_comment_id?: string | null;
  author: PaperCommentAuthor;
  body: string;
  created_at: string;
  updated_at: string;
  replies: PaperComment[];
}

export interface PaperCommentsResponse {
  paper_id: string;
  comments: PaperComment[];
}

export interface TrendingPaperCard {
  paper_id: string;
  arxiv_id: string;
  title: string;
  authors: string;
  published: string;
  html_url: string;
  pdf_url: string;
  image_url?: string | null;
  upvote_count: number;
  viewer_has_upvoted: boolean;
}

export interface TrendingPaperDetail {
  paper_id: string;
  arxiv_id: string;
  title: string;
  authors: string;
  published: string;
  primary_category: string;
  categories: string;
  summary_eli5: string;
  html_url: string;
  pdf_url: string;
  image_url?: string | null;
  upvote_count: number;
  viewer_has_upvoted: boolean;
  comments: PaperComment[];
}

export interface TrendingFeedResponse {
  date: string;
  sort: "default" | "upvotes";
  papers: TrendingPaperCard[];
}

export interface UploadedDocumentResponse {
  document_id: string;
  user_id: string;
  title: string;
  source_filename: string;
  content_type: string;
  tags: string[];
  status: string;
  created_at: string;
  updated_at: string;
}

export interface UploadListResponse {
  documents: UploadedDocumentResponse[];
  available_tags: string[];
}

export interface ChatCitation {
  source_id: string;
  source_kind: string;
  title: string;
  path: string;
  excerpt: string;
  paper_id?: string | null;
  document_id?: string | null;
  tags: string[];
}

export interface ChatMessageResponse {
  message_id: string;
  role: string;
  content_markdown: string;
  citations: ChatCitation[];
  created_at: string;
  status: string;
  kind?: "default" | "compaction" | string;
  phase?: "commentary" | "final_answer" | null;
}

export type ChatItemType =
  | "user_message"
  | "assistant_message"
  | "reasoning"
  | "function_call"
  | "function_call_output"
  | "custom_tool_call"
  | "custom_tool_call_output"
  | "tool_search_call"
  | "tool_search_output"
  | "web_search_call"
  | "compaction"
  | "error"
  | "other";

export interface ChatThreadItemResponse {
  item_id: string;
  thread_id: string;
  turn_id: string;
  item_type: ChatItemType | string;
  role: string;
  phase?: "commentary" | "final_answer" | null;
  call_id?: string | null;
  name?: string | null;
  content_text: string;
  content_json?: unknown;
  citations: ChatCitation[];
  created_at: string;
  status: string;
}

export interface ChatTurnResponse {
  turn_id: string;
  thread_id: string;
  status: string;
  provider_response_id: string;
  started_at: string;
  completed_at?: string | null;
  usage?: Record<string, unknown> | null;
  diff_summary: string;
  changed_paths: string[];
  exec_session_ids: string[];
  items: ChatThreadItemResponse[];
}

export interface ChatThreadSummary {
  thread_id: string;
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_message_preview: string;
}

export interface ChatThreadResponse {
  thread_id: string;
  project_id: string;
  project_name: string;
  project_instructions: string;
  title: string;
  created_at: string;
  updated_at: string;
  items?: ChatThreadItemResponse[];
  turns?: ChatTurnResponse[];
  messages?: ChatMessageResponse[];
}

export interface ChatThreadListResponse {
  threads: ChatThreadSummary[];
}

export interface ProjectSummary {
  project_id: string;
  name: string;
  description: string;
  is_default: boolean;
  session_count: number;
  github_repo_url: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectResponse extends ProjectSummary {}

export type ProjectFileKind = "python" | "notebook" | "text";
export type ProjectRunMode = "smoke" | "full";

export interface CreateProjectGitHubRepoPayload {
  mode: "existing" | "create";
  owner?: string;
  name: string;
  base_branch?: string;
  import_paths?: string[];
}

export interface ProjectListResponse {
  projects: ProjectSummary[];
}

export interface ProjectSessionPageResponse {
  project_id: string;
  threads: ChatThreadSummary[];
  offset: number;
  limit: number;
  total_count: number;
  has_more: boolean;
}

export interface PythonFileSummary {
  file_id: string;
  project_id: string;
  filename: string;
  file_kind: ProjectFileKind;
  repo_path: string;
  created_at: string;
  updated_at: string;
}

export interface PythonFileResponse extends PythonFileSummary {
  content: string;
}

export type ProjectItemKind = "thread" | "python_file";

export interface ProjectItemSummary {
  item_id: string;
  item_kind: ProjectItemKind;
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  thread_id?: string | null;
  file_id?: string | null;
  last_message_preview: string;
  filename: string;
  file_kind: ProjectFileKind;
  repo_path: string;
}

export interface ProjectItemPageResponse {
  project_id: string;
  items: ProjectItemSummary[];
  offset: number;
  limit: number;
  total_count: number;
  has_more: boolean;
}

export interface RunPythonFileResponse {
  file_id: string;
  status: "completed" | "failed";
  stdout: string;
  stderr: string;
  exit_code: number;
  started_at: string;
  finished_at: string;
}

export interface ProjectRepoConflictResolution {
  path: string;
  content: string;
}

export interface ProjectRepoConflictFile {
  path: string;
  file_kind: ProjectFileKind;
  base_content: string;
  local_content: string;
  remote_content: string;
}

export interface ProjectRepoConflictDetail {
  code: "github_merge_conflict";
  message: string;
  project_id: string;
  repo_exists: boolean;
  github_owner_login: string;
  github_repo_name: string;
  github_repo_url: string;
  base_branch: string;
  default_branch: string;
  base_commit_sha: string;
  remote_head_sha: string;
  conflicts: ProjectRepoConflictFile[];
}

export interface ProjectRepoSyncResponse {
  project_id: string;
  repo_exists: boolean;
  github_owner_login: string;
  github_repo_name: string;
  github_repo_url: string;
  base_branch: string;
  default_branch: string;
  last_sync_commit_sha: string;
  last_sync_commit_url: string;
  last_synced_at?: string | null;
}

export interface GitHubRepoBranchSummary {
  name: string;
  commit_sha: string;
  is_default: boolean;
}

export interface GitHubRepoSummary {
  owner: string;
  name: string;
  full_name: string;
  html_url: string;
  default_branch: string;
  private: boolean;
}

export interface GitHubRepoListResponse {
  repos: GitHubRepoSummary[];
}

export interface GitHubRepoMetadataResponse {
  owner: string;
  name: string;
  html_url: string;
  default_branch: string;
  branches: GitHubRepoBranchSummary[];
}

export interface GitHubRepoImportCandidate {
  path: string;
  file_kind: ProjectFileKind;
  size: number;
}

export interface GitHubRepoImportCandidateResponse {
  owner: string;
  name: string;
  base_branch: string;
  candidates: GitHubRepoImportCandidate[];
}

export interface SendChatMessageResponse {
  thread: ChatThreadResponse;
  turn?: ChatTurnResponse | null;
  final_item?: ChatThreadItemResponse | null;
  assistant_message?: ChatMessageResponse | null;
  has_openai_api_key: boolean;
  has_anthropic_api_key?: boolean;
  state: string;
}

export interface ChatStreamTurnStartedEvent {
  turn: ChatTurnResponse;
}

export interface ChatStreamItemStartedEvent {
  item: ChatThreadItemResponse;
}

export interface ChatStreamItemDeltaEvent {
  item_id: string;
  delta: string;
  delta_type: string;
}

export interface ChatStreamItemCompletedEvent {
  item: ChatThreadItemResponse;
}

export interface ChatStreamUsageEvent {
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  context_window_tokens: number;
}

export interface ChatStreamTurnCompletedEvent {
  thread: ChatThreadResponse;
  turn: ChatTurnResponse;
  final_item?: ChatThreadItemResponse | null;
  has_openai_api_key: boolean;
  has_anthropic_api_key?: boolean;
  state: "completed";
  usage?: ChatStreamUsageEvent | null;
}

export interface ChatStreamTurnFailedEvent {
  turn: ChatTurnResponse;
  detail: string;
}

export type ChatStreamDoneEvent = ChatStreamTurnCompletedEvent;

export interface ChatStreamErrorEvent {
  message_id?: string;
  detail: string;
}

export interface ChatStreamMentionIndexingEvent {
  paper_id: string;
  arxiv_id?: string;
  title?: string;
  detail?: string;
}

export type ChatStreamEventMap = {
  turn_started: ChatStreamTurnStartedEvent;
  item_started: ChatStreamItemStartedEvent;
  item_delta: ChatStreamItemDeltaEvent;
  item_completed: ChatStreamItemCompletedEvent;
  usage: ChatStreamUsageEvent;
  turn_completed: ChatStreamTurnCompletedEvent;
  turn_failed: ChatStreamTurnFailedEvent;
  api_key_required: SendChatMessageResponse;
  error: ChatStreamErrorEvent;
  mention_indexing_started: ChatStreamMentionIndexingEvent;
  mention_indexing_completed: ChatStreamMentionIndexingEvent;
  mention_indexing_failed: ChatStreamMentionIndexingEvent;
};

export interface MentionTag {
  id: string;
  title: string;
}

export interface MentionSuggestion {
  paper_id: string;
  arxiv_id: string;
  title: string;
  authors: string;
  summary_short: string;
  citation_count: number;
  published: string;
  score: number;
  matched_field: string;
  highlight_snippet: string;
}

export interface MentionSuggestionResult {
  suggestions: MentionSuggestion[];
  fallback: boolean;
}
