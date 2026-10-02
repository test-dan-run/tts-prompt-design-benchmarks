import { useEffect, useState } from "react";
import { api } from "../api";
import { fmtBytes } from "../format";
import type { ModelInfo } from "../types";

interface Props {
  models: ModelInfo[];
  pending: Record<string, boolean>;
  onStart: (m: ModelInfo) => void;
  onStop: (m: ModelInfo) => void;
  onDownload: (m: ModelInfo) => void;
  onCancelDownload: (m: ModelInfo) => void;
  onVerify: (m: ModelInfo) => void;
  onRemove: (m: ModelInfo, deleteWeights: boolean) => void;
  onAdd: () => void;
}

const STATE_LABEL: Record<string, string> = {
  stopped: "Stopped",
  starting: "Loading…",
  ready: "Ready",
  error: "Failed",
  unavailable: "Not in registry",
};

function Elapsed({ since }: { since?: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  if (!since) return null;
  const s = Math.max(0, Math.round((Date.now() - new Date(since).getTime()) / 1000));
  return <span className="muted"> {s}s</span>;
}

function VerifyBadge({ m, busy, onVerify }: { m: ModelInfo; busy: boolean; onVerify: () => void }) {
  if (!m.in_registry) return null;
  if (busy) return <span className="tag">checking…</span>;
  const v = m.verification;
  if (!v)
    return (
      <button className="tag tag--btn" onClick={onVerify} title="Check this model against the pinned vLLM-Omni build">
        verify
      </button>
    );
  const label = { ok: "✓", warning: "!", error: "✗" }[v.status];
  const title = [
    `vLLM-Omni ${v.vllm_omni_version}: ${v.status}`,
    `Voice prompt: ${v.voice_design}`,
    ...v.messages,
    `Checked ${new Date(v.checked_at).toLocaleString()}. Click to re-check.`,
  ].join("\n");
  return (
    <button className={`tag tag--btn tag--${v.status}`} title={title} onClick={onVerify}>
      {label} vLLM-Omni {v.vllm_omni_version}
    </button>
  );
}

export function ModelsPanel(props: Props) {
  const { models, pending } = props;
  const [logsFor, setLogsFor] = useState<string | null>(null);
  const [logs, setLogs] = useState("");
  const [removing, setRemoving] = useState<string | null>(null);
  const running = models.find((m) => m.state === "ready" || m.state === "starting");

  useEffect(() => {
    if (!logsFor) return;
    let alive = true;
    const load = () => api.logs(logsFor).then((t) => alive && setLogs(t || "(no container)"));
    load();
    const t = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [logsFor]);

  return (
    <section className="panel">
      <header className="panel__head">
        <h2>Models</h2>
        <button className="btn btn--sm" onClick={props.onAdd}>
          + Add model
        </button>
        <span className="muted small panel__sub">One backend runs at a time. Clips are kept after it stops.</span>
      </header>
      <ul className="models">
        {models.map((m) => {
          const busy = pending[m.key];
          const live = m.state === "ready" || m.state === "starting";
          const dl = m.download;
          const downloading = dl?.state === "running";
          const missing = m.downloaded === false && !downloading;
          const switching = running && running.key !== m.key;
          return (
            <li key={m.key} className={`model model--${m.state}`}>
              <div className="model__top">
                <div className="model__main">
                  <div className="model__name">
                    <span className={`dot dot--${m.state}`} />
                    {m.name}
                    {m.experimental && <span className="tag">experimental</span>}
                    {m.source === "custom" && <span className="tag">added</span>}
                  </div>
                  <div className="model__sub muted small">
                    {STATE_LABEL[m.state] ?? m.state}
                    {m.state === "starting" && m.phase && <span> ({m.phase})</span>}
                    {m.state === "starting" && <Elapsed since={m.started_at} />}
                    {" · "}
                    {m.library.clips} clip{m.library.clips === 1 ? "" : "s"}
                    {missing && <span className="warn"> · weights missing</span>}
                    {m.state === "ready" && m.api_base_host && <> · API {m.api_base_host}</>}
                  </div>
                  <div className="model__badges">
                    <VerifyBadge m={m} busy={!!pending[`verify:${m.key}`]} onVerify={() => props.onVerify(m)} />
                  </div>
                </div>
                <div className="model__actions">
                  {m.in_registry && (live || m.state === "error") && (
                    <button
                      className="btn btn--ghost btn--sm"
                      onClick={() => setLogsFor(logsFor === m.key ? null : m.key)}
                    >
                      Logs
                    </button>
                  )}
                  {m.in_registry && missing && (
                    <button className="btn btn--sm" disabled={busy} onClick={() => props.onDownload(m)}>
                      Download
                    </button>
                  )}
                  {m.in_registry &&
                    !missing &&
                    !downloading &&
                    (live ? (
                      <button className="btn btn--sm" disabled={busy} onClick={() => props.onStop(m)}>
                        Stop
                      </button>
                    ) : (
                      <button
                        className="btn btn--primary btn--sm"
                        disabled={busy}
                        title={switching ? `Starting this stops ${running!.name}` : undefined}
                        onClick={() => props.onStart(m)}
                      >
                        {busy ? "…" : switching ? "Switch" : "Start"}
                      </button>
                    ))}
                  {m.source === "custom" && !live && (
                    <button
                      className="icon-btn icon-btn--danger"
                      title="Remove model"
                      onClick={() => setRemoving(removing === m.key ? null : m.key)}
                    >
                      ×
                    </button>
                  )}
                </div>
              </div>

              {downloading && (
                <div className="progress">
                  <div className="progress__bar">
                    <div
                      className="progress__fill"
                      style={{
                        width: dl.total ? `${Math.min(100, ((dl.bytes ?? 0) / dl.total) * 100)}%` : "100%",
                      }}
                      data-indeterminate={!dl.total || undefined}
                    />
                  </div>
                  <span className="small muted">
                    Downloading {fmtBytes(dl.bytes)} / {fmtBytes(dl.total)}
                  </span>
                  <button className="link small" onClick={() => props.onCancelDownload(m)}>
                    Cancel
                  </button>
                </div>
              )}
              {dl?.state === "error" && (
                <div className="model__error small">
                  Download failed.{" "}
                  <button className="link" onClick={() => props.onDownload(m)}>
                    Retry
                  </button>
                  <pre>{dl.error}</pre>
                </div>
              )}
              {removing === m.key && (
                <div className="confirm small">
                  Remove {m.name}? Its clips stay in the library.
                  <div className="confirm__actions">
                    <button
                      className="btn btn--sm"
                      onClick={() => {
                        setRemoving(null);
                        props.onRemove(m, false);
                      }}
                    >
                      Remove
                    </button>
                    <button
                      className="btn btn--sm btn--danger"
                      onClick={() => {
                        setRemoving(null);
                        props.onRemove(m, true);
                      }}
                    >
                      Remove and delete weights
                    </button>
                    <button className="btn btn--ghost btn--sm" onClick={() => setRemoving(null)}>
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {logsFor && (
        <div className="logs">
          <div className="logs__head">
            <strong>{logsFor}</strong> logs
            <button className="icon-btn" onClick={() => setLogsFor(null)}>
              ×
            </button>
          </div>
          <pre>{logs}</pre>
        </div>
      )}
    </section>
  );
}
