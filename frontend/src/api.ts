export interface Topic {
  title: string;
  angle: string;
  reason: string;
}
export interface Article {
  title: string;
  body: string;
}
export interface ContentState {
  brief: {
    direction: string;
    audience: string;
    references: string;
    topic_count: number;
    card_count: number;
  };
  stage?: string;
  topics?: Topic[];
  article?: Article | null;
  article_revision?: number;
  package?: {
    cards: { title: string; body: string }[];
    title: string;
    caption: string;
  } | null;
  assets?: string[];
  render_error?: string | null;
  bundle?: string | null;
}
export interface TaskSummary {
  id: string;
  direction: string;
  status: string;
  error: string | null;
  created_at: string;
}
export interface Task extends TaskSummary {
  state: ContentState;
  interrupt_id: string | null;
}
export interface History {
  checkpoint_id: string;
  state: ContentState;
  created_at: string;
}

export interface NodeRun {
  id: number;
  job_id: number;
  subgraph: string;
  node_name: string;
  status: "running" | "completed" | "waiting" | "failed" | "interrupted";
  started_at: string;
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  model: string | null;
}

export async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `/api/v1${path}`,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : `请求失败（${response.status}），请检查输入或服务状态`,
    );
  }
  return response.json();
}

export function assetUrl(path: string) {
  return "/assets/" + path.split("/").map(encodeURIComponent).join("/");
}
