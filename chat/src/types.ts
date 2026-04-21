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

export interface MentionTag {
  id: string;
  title: string;
}
