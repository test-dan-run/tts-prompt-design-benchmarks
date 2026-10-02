import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { fmtBytes } from "../format";
import type { SystemInfo, Verification } from "../types";

interface Props {
  system: SystemInfo | null;
  onClose: () => void;
  onAdded: (name: string) => void;
}

const VOICE_DESIGN_LABEL = {
  native: "Documented voice design",
  instructions: "Accepts instructions (voice design unverified)",
  text_prefix: "Description prefixed to text (unverified)",
  none: "No voice prompt support",
};

function Check({ ok, warn, children }: { ok: boolean | null; warn?: boolean; children: React.ReactNode }) {
  const cls = ok === null ? "unknown" : !ok ? "bad" : warn ? "warn" : "good";
  const icon = ok === null ? "?" : !ok ? "✗" : warn ? "!" : "✓";
  return (
    <li className={`check-row check-row--${cls}`}>
      <span className="check-row__icon">{icon}</span>
      <div>{children}</div>
    </li>
  );
}

export function AddModelDialog({ system, onClose, onAdded }: Props) {
  const [repo, setRepo] = useState("");
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<Verification | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("");
  const [serveArgs, setServeArgs] = useState("");
  const [adding, setAdding] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const version = system?.vllm_omni_version ?? "?";

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const check = async (refresh = false) => {
    if (!repo.trim()) return;
    setChecking(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.verify(repo.trim(), refresh);
      setResult(r);
      setRepo(r.repo);
      setKey(r.suggestion.key);
      setName(r.suggestion.name);
      setTemplate(JSON.stringify(r.suggestion.request, null, 2));
      setServeArgs(r.suggestion.serve_args.join(" "));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  };

  let templateError: string | null = null;
  let parsed: Record<string, unknown> | null = null;
  if (template) {
    try {
      parsed = JSON.parse(template);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) templateError = "Must be a JSON object";
      else if (!JSON.stringify(parsed).includes("{text}")) templateError = "Must contain {text}";
    } catch (e) {
      templateError = (e as Error).message;
    }
  }

  const add = async () => {
    if (!result || !parsed || templateError) return;
    setAdding(true);
    setError(null);
    try {
      await api.addModel({
        repo: result.repo,
        key,
        name,
        request: parsed,
        serve_args: serveArgs.split(/\s+/).filter(Boolean),
        aux_repos: result.suggestion.aux_repos,
        download: true,
      });
      onAdded(name);
    } catch (e) {
      setError((e as Error).message);
      setAdding(false);
    }
  };

  const v = result?.verdict;
  const hub = result?.hub;
  const probe = result?.probe;

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="add-model-title">
        <header className="modal__head">
          <h2 id="add-model-title">Add a model</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>
        <p className="muted small">
          Models are checked against the backend image in use ({system?.backend_image ?? "?"}, vLLM-Omni {version}).
          The check runs vLLM-Omni's own model resolution and downloads only config files.
        </p>
        <form
          className="repo-row"
          onSubmit={(e) => {
            e.preventDefault();
            void check();
          }}
        >
          <input
            ref={inputRef}
            className="input"
            placeholder="Hugging Face repo, e.g. OpenMOSS-Team/MOSS-VoiceGenerator"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            disabled={checking}
          />
          <button className="btn btn--primary" disabled={checking || !repo.trim()}>
            {checking ? "Checking…" : "Check compatibility"}
          </button>
        </form>
        {checking && <p className="muted small">Resolving with vLLM-Omni {version}. This takes about 30 seconds.</p>}
        {error && <p className="form-error">{error}</p>}

        {result && v && hub && probe && (
          <>
            <div className={`verdict verdict--${v.status}`}>
              <strong>
                {v.status === "ok"
                  ? `Compatible with vLLM-Omni ${result.vllm_omni_version}`
                  : v.status === "warning"
                    ? `Compatible with vLLM-Omni ${result.vllm_omni_version}, with caveats`
                    : `Not usable with vLLM-Omni ${result.vllm_omni_version}`}
              </strong>
              <span className="muted small">
                {result.cached ? "Cached result from " : "Checked "}
                {new Date(result.checked_at).toLocaleString()} ·{" "}
                <button className="link" onClick={() => check(true)}>
                  re-check
                </button>
              </span>
            </div>
            <ul className="checks">
              <Check ok={hub.exists}>
                Found on Hugging Face
                {hub.exists && (
                  <span className="muted">
                    {" "}
                    · {fmtBytes(hub.size_bytes)}
                    {hub.license ? ` · ${hub.license}` : ""}
                    {hub.gated ? " · gated" : ""}
                  </span>
                )}
                {hub.error && <div className="muted small">{hub.error}</div>}
              </Check>
              <Check ok={hub.exists === false ? null : probe.supported}>
                Resolves to a vLLM-Omni {result.vllm_omni_version} pipeline
                {probe.deploy_config && (
                  <span className="muted"> · {probe.deploy_config.split("/").pop()}</span>
                )}
                {probe.config?.architectures && (
                  <div className="muted small">{probe.config.architectures.join(", ")}</div>
                )}
                {!probe.supported && probe.error && <div className="muted small">{probe.error}</div>}
              </Check>
              <Check ok={probe.supported ? probe.tts : null}>
                Served through /v1/audio/speech
                {probe.tts_model_type && <span className="muted"> · {probe.tts_model_type} adapter</span>}
                {probe.note && <div className="muted small">{probe.note}</div>}
              </Check>
              {result.aux_repos.length > 0 && (
                <Check ok={true}>
                  Needs auxiliary repos at runtime, downloaded with the model
                  <div className="muted small">
                    {result.aux_repos.map((a) => `${a.repo} (${fmtBytes(a.size_bytes)})`).join(", ")}
                  </div>
                </Check>
              )}
              <Check ok={probe.tts ? v.voice_design !== "none" : null} warn={v.voice_design !== "native"}>
                {VOICE_DESIGN_LABEL[v.voice_design]}
              </Check>
            </ul>
            {v.messages.filter((m) => !m.startsWith("also needs")).length > 0 && (
              <ul className="messages small">
                {v.messages.filter((m) => !m.startsWith("also needs")).map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            )}

            {v.addable && (
              <div className="add-form">
                <div className="add-form__row">
                  <label className="field">
                    <span>Display name</span>
                    <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
                  </label>
                  <label className="field">
                    <span>Key</span>
                    <input className="input" value={key} onChange={(e) => setKey(e.target.value)} />
                  </label>
                </div>
                <label className="field">
                  <span>
                    Request template for /v1/audio/speech. Use <code>{"{prompt}"}</code> and <code>{"{text}"}</code>.
                  </span>
                  <textarea
                    className="mono"
                    rows={6}
                    value={template}
                    onChange={(e) => setTemplate(e.target.value)}
                    spellCheck={false}
                  />
                  {templateError && <span className="form-error small">{templateError}</span>}
                </label>
                <label className="field">
                  <span>Extra vllm serve arguments</span>
                  <input className="input mono" value={serveArgs} onChange={(e) => setServeArgs(e.target.value)} />
                </label>
                <div className="modal__actions">
                  <button className="btn" onClick={onClose}>
                    Cancel
                  </button>
                  <button
                    className="btn btn--primary"
                    disabled={adding || !!templateError || !key || !name}
                    onClick={() => void add()}
                  >
                    {adding ? "Adding…" : `Add & download (${fmtBytes(result.total_bytes)})`}
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
