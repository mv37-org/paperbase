export type AuthView = "login" | "signup" | "reset-request" | "reset-password" | "settings";

export interface BrowserUser {
  user_id: string;
  email: string;
  display_name: string;
  free_responses_used: number;
}

export interface BrowserUserSettingsResponse {
  anthropic_api_key_hint: string;
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
  remaining_free_responses: number;
  has_anthropic_api_key: boolean;
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

export interface FileContentResponse {
  paper_id: string;
  path: string;
  content: string;
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
  messages: ChatMessageResponse[];
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
  created_at: string;
  updated_at: string;
}

export interface ProjectResponse extends ProjectSummary {}

export type ProjectFileKind = "python" | "notebook";

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

export interface ProjectItemSummary {
  item_id: string;
  item_kind: "thread" | "python_file";
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
  assistant_message: ChatMessageResponse | null;
  remaining_free_responses: number;
  has_anthropic_api_key: boolean;
  state: string;
}

export interface ChatStreamAssistantStartEvent {
  assistant_message: ChatMessageResponse;
}

export interface ChatStreamDeltaEvent {
  message_id: string;
  delta: string;
}

export interface ChatStreamUsageEvent {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
  context_window_tokens: number;
}

export interface ChatStreamDoneEvent {
  assistant_message: ChatMessageResponse;
  remaining_free_responses: number;
  has_anthropic_api_key: boolean;
  state: "completed";
  usage?: ChatStreamUsageEvent | null;
}

export interface ChatStreamErrorEvent {
  message_id?: string;
  detail: string;
}

export interface ChatStreamToolStartEvent {
  tool_call_id: string;
  tool_name: string;
  label: string;
}

export interface ChatStreamToolDoneEvent {
  tool_call_id: string;
  tool_name: string;
  label: string;
  is_error: boolean;
}

export interface ChatStreamCompactionStartEvent {
  compaction_message: ChatMessageResponse;
}

export interface ChatStreamCompactionDoneEvent {
  compaction_message: ChatMessageResponse;
  messages: ChatMessageResponse[];
}

export type ChatStreamEventMap = {
  compaction_start: ChatStreamCompactionStartEvent;
  compaction_done: ChatStreamCompactionDoneEvent;
  tool_start: ChatStreamToolStartEvent;
  tool_done: ChatStreamToolDoneEvent;
  assistant_start: ChatStreamAssistantStartEvent;
  usage: ChatStreamUsageEvent;
  delta: ChatStreamDeltaEvent;
  done: ChatStreamDoneEvent;
  api_key_required: SendChatMessageResponse;
  error: ChatStreamErrorEvent;
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
