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
  source?: "builtin" | "custom";
  download?: DownloadStatus | null;
  verification?: VerificationSummary | null;
}

export type VoiceDesign = "native" | "instructions" | "text_prefix" | "none";

export interface DownloadStatus {
  state: "running" | "done" | "error" | "cancelled";
  bytes?: number;
  total?: number | null;
  error?: string;
}

export interface VerificationSummary {
  status: "ok" | "warning" | "error";
  voice_design: VoiceDesign;
  vllm_omni_version: string | null;
  checked_at: string;
  messages: string[];
}

export interface Verification {
  repo: string;
  checked_at: string;
  cached: boolean;
  backend_image: string;
  vllm_omni_version: string | null;
  hub: {
    exists: boolean | null;
    error?: string;
    gated?: boolean;
    license?: string | null;
    size_bytes?: number;
    downloads?: number;
    pipeline_tag?: string | null;
  };
  probe: {
    supported: boolean;
    tts: boolean;
    tts_model_type?: string | null;
    adapter?: string;
    deploy_config?: string | null;
    config?: { architectures?: string[]; model_type?: string };
    error?: string;
    note?: string;
  };
  verdict: {
    addable: boolean;
    status: "ok" | "warning" | "error";
    voice_design: VoiceDesign;
    summary: string;
    messages: string[];
  };
  aux_repos: { repo: string; size_bytes: number | null; gated?: boolean }[];
  total_bytes: number;
  suggestion: {
    key: string;
    name: string;
    request: Record<string, unknown>;
    serve_args: string[];
    aux_repos: string[];
  };
}

export interface SystemInfo {
  backend_image: string;
  vllm_omni_version: string | null;
  exclusive_backends: boolean;
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
