export type AuthView = "login" | "signup" | "reset-request" | "reset-password" | "api-key";

export interface BrowserUser {
  user_id: string;
  email: string;
  display_name: string;
  free_responses_used: number;
}

export interface BrowserSessionResponse {
  authenticated: boolean;
  user: BrowserUser | null;
  remaining_free_responses: number;
  has_anthropic_api_key: boolean;
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
}

export interface ChatThreadSummary {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_message_preview: string;
}

export interface ChatThreadResponse {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: ChatMessageResponse[];
}

export interface ChatThreadListResponse {
  threads: ChatThreadSummary[];
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

export interface ChatStreamDoneEvent {
  assistant_message: ChatMessageResponse;
  remaining_free_responses: number;
  has_anthropic_api_key: boolean;
  state: "completed";
}

export interface ChatStreamErrorEvent {
  message_id?: string;
  detail: string;
}

export type ChatStreamEventMap = {
  assistant_start: ChatStreamAssistantStartEvent;
  delta: ChatStreamDeltaEvent;
  done: ChatStreamDoneEvent;
  api_key_required: SendChatMessageResponse;
  error: ChatStreamErrorEvent;
};

export interface MentionTag {
  id: string;
  title: string;
}
