import type {
  ChatMessageResponse,
  ChatStreamUsageEvent,
  FigureResponse,
  FileContentResponse,
  MentionTag,
  PaperPreviewResponse,
  PaperResponse,
  ProjectItemKind,
  ProjectItemSummary,
  ProjectRepoConflictFile,
  ProjectRepoSyncResponse,
  RunPythonFileResponse,
  TableResponse,
} from "../types";

export type ModalFlashKind = "error" | "note";

export type PaperDrawerState = { paperId: string; path: string } | null;

export type PaperDrawerContent =
  | { kind: "markdown"; path: string; file: FileContentResponse }
  | { kind: "figure"; path: string; figure: FigureResponse }
  | { kind: "table"; path: string; table: TableResponse };

export type PaperCitationPreviewState = {
  paper: PaperResponse | null;
  preview: PaperPreviewResponse | null;
  status: "loading" | "ready" | "error";
  error: string;
};

export type PaperActionTarget = {
  paperId: string;
  path: string;
  paper?: PaperResponse | null;
  title?: string;
  authors?: string;
  previewImageUrl?: string | null;
  pdfUrl?: string | null;
  projectId?: string | null;
};

export type PaperPdfModalState = {
  paperId: string;
  path: string;
  paper: PaperResponse | null;
  title: string;
  authors: string;
  pdfUrl: string;
  status: "loading" | "ready" | "error";
  error: string;
};

export type PaperAnnotationRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type PaperAnnotationPoint = {
  x: number;
  y: number;
};

export type PaperAnnotationTarget = {
  paperId: string;
  pageNumber: number;
  selectedText?: string | null;
  nearbyText?: string | null;
  sectionTitle?: string | null;
  rects?: PaperAnnotationRect[];
  region?: {
    points: PaperAnnotationPoint[];
    bbox: PaperAnnotationRect;
  } | null;
};

export type PaperExplorationAnnotation = PaperAnnotationTarget & {
  annotationId: string;
  kind: "text" | "region";
  note: string;
  createdAt: string;
  updatedAt: string;
};

export type PaperExploration = {
  explorationId: string;
  projectId: string;
  paperId: string;
  chatThreadId?: string;
  annotations: PaperExplorationAnnotation[];
  path: string;
  title: string;
  authors: string;
  previewImageUrl: string;
  pdfUrl: string;
  createdAt: string;
  updatedAt: string;
};

export type OpenTab =
  | { kind: "thread"; id: string }
  | { kind: "python_file"; id: string }
  | { kind: "project_home"; id: string }
  | { kind: "paper_exploration"; id: string };

export type WorkspaceView =
  | { kind: "trending" }
  | { kind: "admin_jobs" }
  | { kind: "admin_job_papers"; rootJobId: string };

export type ProjectSidebarCategoryKey = "notes" | "sessions" | "code";

export type ProjectItemState = {
  items: ProjectItemSummary[];
  totalCount: number;
  hasMore: boolean;
  hasMoreByKind?: Partial<Record<ProjectItemKind, boolean>>;
  loaded: boolean;
  loading: boolean;
  loadingMore: boolean;
  loadingMoreByKind?: Partial<Record<ProjectItemKind, boolean>>;
  totalCountByKind?: Partial<Record<ProjectItemKind, number>>;
  error: string;
};

export type PythonFileDraft = {
  content: string;
  dirty: boolean;
};

export type PythonFileRunState = {
  running: boolean;
  result: RunPythonFileResponse | null;
  error: string;
};

export type SaveIntent = "save" | "run";

export type SaveModalState = {
  fileId: string;
  commitMessage: string;
  saving: boolean;
  error: string;
  repoSync: ProjectRepoSyncResponse | null;
  success: ProjectRepoSyncResponse | null;
} | null;

export type SyncConflictState = {
  fileId: string;
  projectId: string;
  commitMessage: string;
  expectedRemoteHeadSha: string;
  repoName: string;
  repoUrl: string;
  defaultBranch: string;
  baseCommitSha: string;
  conflicts: ProjectRepoConflictFile[];
  selectedPath: string;
  resolutions: Record<string, string>;
  afterSaveAction: SaveIntent;
  saving: boolean;
  error: string;
} | null;

export type ProjectModalState = { mode: "create" } | { mode: "edit"; projectId: string };

export type DeleteModalState =
  | { kind: "project"; projectId: string; name: string }
  | { kind: "thread"; projectId: string; threadId: string; title: string }
  | { kind: "python_file"; projectId: string; fileId: string; title: string }
  | { kind: "paper_exploration"; explorationId: string; title: string }
  | { kind: "paper_annotation"; explorationId: string; annotationId: string; title: string }
  | null;

export type ThreadPane = "chat" | "papers";

export type FeedbackSentiment = "positive" | "negative";

export type FeedbackModalState = {
  message: ChatMessageResponse;
  sentiment: FeedbackSentiment;
} | null;

export type ExportFormat = "pdf" | "markdown" | "docx";

export type ExportModalState = { source: "header" | "message"; messageId?: string } | null;

export type ToolActivityEntry = {
  id: string;
  turnIndex: number;
  toolName?: string;
  label: string;
  command: string | null;
  output: string | null;
  status: "running" | "completed";
  startedAt: string;
  finishedAt: string | null;
  isError: boolean;
};

export type ThreadStreamState = {
  turnIndex: number;
  thinkingWord: string;
  replyTitle: string | null;
  assistantStarted: boolean;
  usage: ChatStreamUsageEvent | null;
};

export type AppearanceTheme = "light" | "dark";

export type SettingsTab = "inference" | "audio" | "sandbox" | "version-control" | "appearance";

export type PendingPromptState = {
  prompt: string;
  paperIds: string[];
  mentions: MentionTag[];
};
