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
  owner_id: string;
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

export type TokenSource = "api_key" | "subscription" | "demo";

export interface User {
  id: string;
  token_source: TokenSource;
  model: string;
  provider: string;
  base_url: string | null;
  key_fingerprint: string;
  account_email: string;
  created_at: number;
}

export interface AuthMethods {
  free: boolean;
  free_model: string;
  forced_model: string;
  demo: boolean;
  default_provider: string;
  subscription_vendor: string;
  providers: string[];
}

export interface DeviceStart {
  attempt_id: string;
  user_code: string;
  verification_uri: string;
  verification_uri_complete: string | null;
  expires_at: number | null;
  interval_seconds: number;
}
