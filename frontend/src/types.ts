export type BackendState = "stopped" | "starting" | "ready" | "error" | "unavailable";

export interface ModelInfo {
  key: string;
  name: string;
  hf_repo?: string;
  state: BackendState;
  in_registry: boolean;
  experimental?: boolean;
  notes?: string;
  downloaded?: boolean | null;
  host_port?: number | null;
  api_base_host?: string | null;
  started_at?: string;
  phase?: string;
  exit_code?: number;
  logs_tail?: string;
  library: { clips: number; samples: number; mean_rtf?: number | null };
}

export interface Clip {
  id: string;
  sample_id: string;
  model: string;
  model_name: string | null;
  hf_repo: string | null;
  backend_image: string | null;
  created_at: string;
  request: Record<string, unknown> | null;
  format: string;
  bytes: number | null;
  latency_s: number | null;
  audio_duration_s: number | null;
  rtf: number | null;
  sample_rate: number | null;
  tag: string | null;
  audio_url: string;
}

export interface Sample {
  id: string;
  prompt: string;
  text: string;
  label: string | null;
  created_at: string;
  clips: Clip[];
}

export interface Suite {
  name: string;
  cases: number;
}
