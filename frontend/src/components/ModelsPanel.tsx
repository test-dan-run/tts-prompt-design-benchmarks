import { useEffect, useState } from "react";
import { api } from "../api";
import type { ModelInfo } from "../types";

interface Props {
  models: ModelInfo[];
  pending: Record<string, boolean>;
  onStart: (m: ModelInfo) => void;
  onStop: (m: ModelInfo) => void;
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

export function ModelsPanel({ models, pending, onStart, onStop }: Props) {
  const [logsFor, setLogsFor] = useState<string | null>(null);
  const [logs, setLogs] = useState("");
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
        <span className="muted small">One backend runs at a time. Clips are kept after it stops.</span>
      </header>
      <ul className="models">
        {models.map((m) => {
          const busy = pending[m.key];
          const live = m.state === "ready" || m.state === "starting";
          const blocked = m.downloaded === false;
          const switching = running && running.key !== m.key;
          return (
            <li key={m.key} className={`model model--${m.state}`}>
              <div className="model__main">
                <div className="model__name">
                  <span className={`dot dot--${m.state}`} />
                  {m.name}
                  {m.experimental && <span className="tag">experimental</span>}
                </div>
                <div className="model__sub muted small">
                  {STATE_LABEL[m.state] ?? m.state}
                  {m.state === "starting" && m.phase && <span> ({m.phase})</span>}
                  {m.state === "starting" && <Elapsed since={m.started_at} />}
                  {" · "}
                  {m.library.clips} clip{m.library.clips === 1 ? "" : "s"}
                  {m.downloaded === false && <span className="warn"> · weights missing</span>}
                  {m.state === "ready" && m.api_base_host && <> · API {m.api_base_host}</>}
                </div>
              </div>
              <div className="model__actions">
                {m.in_registry && (live || m.state === "error") && (
                  <button className="btn btn--ghost btn--sm" onClick={() => setLogsFor(logsFor === m.key ? null : m.key)}>
                    Logs
                  </button>
                )}
                {m.in_registry &&
                  (live ? (
                    <button className="btn btn--sm" disabled={busy} onClick={() => onStop(m)}>
                      Stop
                    </button>
                  ) : (
                    <button
                      className="btn btn--primary btn--sm"
                      disabled={busy || blocked}
                      title={
                        blocked
                          ? `Download first: docker compose run --rm downloader --key ${m.key}`
                          : switching
                            ? `Starting this stops ${running!.name}`
                            : undefined
                      }
                      onClick={() => onStart(m)}
                    >
                      {busy ? "…" : switching ? "Switch" : "Start"}
                    </button>
                  ))}
              </div>
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
