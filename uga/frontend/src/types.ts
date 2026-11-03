export type AppStatus =
  | "created"
  | "building"
  | "deploying"
  | "running"
  | "error"
  | "stopped";

export interface AppRecord {
  id: string;
  name: string;
  prompt: string;
  status: AppStatus;
  stage: string;
  mode: string;
  entry: string;
  internal_port: number | null;
  pid: number | null;
  preview_path: string;
  public_url: string;
  conversation_id: string | null;
  last_error: string;
  build_log: string;
  files: string[];
  created_at: number;
  updated_at: number;
  workspace: string;
}

export interface PipelineEvent {
  kind:
    | "stage"
    | "action"
    | "observation"
    | "message"
    | "info"
    | "error"
    | "success"
    | "done"
    | "ping";
  text: string;
  ts: number;
  tool?: string;
  detail?: string;
  status?: string;
  url?: string;
}
