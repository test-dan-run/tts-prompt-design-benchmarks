import type { Clip, ModelInfo, Sample, Suite } from "./types";

export const API = "/api";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  req<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  models: () => req<ModelInfo[]>("/models"),
  start: (key: string) => post<ModelInfo>(`/backends/${key}/start`),
  stop: (key: string) => post<ModelInfo>(`/backends/${key}/stop`),
  logs: async (key: string) => (await fetch(`${API}/backends/${key}/logs?tail=200`)).text(),

  samples: () => req<Sample[]>("/samples"),
  createSample: (prompt: string, text: string, label?: string) =>
    post<Sample>("/samples", { prompt, text, label: label || null }),
  labelSample: (id: string, label: string | null) =>
    req<Sample>(`/samples/${id}`, { method: "PATCH", body: JSON.stringify({ label }) }),
  deleteSample: (id: string) => req(`/samples/${id}`, { method: "DELETE" }),
  generate: (sampleId: string, model: string, params: Record<string, unknown> = {}) =>
    post<Clip>(`/samples/${sampleId}/generate`, { model, params }),

  deleteClip: (id: string) => req(`/clips/${id}`, { method: "DELETE" }),
  audioUrl: (clip: Clip) => API + clip.audio_url,
  exportUrl: `${API}/clips.csv`,

  suites: () => req<Suite[]>("/suites"),
  importSuite: (name: string) => post<Sample[]>(`/suites/${name}/import`),
};
