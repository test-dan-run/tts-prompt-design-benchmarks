import { useMemo, useState } from "react";
import { api } from "../api";
import { player, usePlayer } from "../player";
import type { Clip, ModelInfo, Sample } from "../types";
import { ClipPlayer } from "./ClipPlayer";

interface Props {
  samples: Sample[];
  models: ModelInfo[]; // every known model (registry + library)
  hidden: Set<string>;
  onToggleModel: (key: string) => void;
  active: ModelInfo | undefined;
  busy: Set<string>; // `${sampleId}:${model}`
  fill: { done: number; total: number } | null;
  onGenerate: (sample: Sample, model: string) => void;
  onFillMissing: (samples: Sample[]) => void;
  onCancelFill: () => void;
  onDeleteClip: (clip: Clip) => void;
  onDeleteSample: (sample: Sample) => void;
  onLabel: (sample: Sample, label: string) => void;
}

const cellKey = (sampleId: string, model: string) => `${sampleId}:${model}`;

function mean(xs: number[]) {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
}

export function CompareGrid(props: Props) {
  const { samples, models, hidden, active, busy, fill } = props;
  const [query, setQuery] = useState("");
  const [onlyComplete, setOnlyComplete] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const p = usePlayer();

  const columns = useMemo(() => models.filter((m) => !hidden.has(m.key)), [models, hidden]);

  const byCell = useMemo(() => {
    const map = new Map<string, Clip[]>();
    for (const s of samples)
      for (const c of s.clips) {
        const k = cellKey(s.id, c.model);
        map.set(k, [...(map.get(k) ?? []), c]);
      }
    for (const list of map.values()) list.sort((a, b) => b.created_at.localeCompare(a.created_at));
    return map;
  }, [samples]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return samples.filter((s) => {
      if (q && !`${s.label ?? ""} ${s.prompt} ${s.text}`.toLowerCase().includes(q)) return false;
      if (onlyComplete && columns.some((m) => !byCell.has(cellKey(s.id, m.key)))) return false;
      return true;
    });
  }, [samples, query, onlyComplete, columns, byCell]);

  const stats = useMemo(() => {
    const out: Record<string, { covered: number; rtf: number | null }> = {};
    for (const m of columns) {
      const latest = rows.map((s) => byCell.get(cellKey(s.id, m.key))?.[0]).filter(Boolean) as Clip[];
      out[m.key] = { covered: latest.length, rtf: mean(latest.map((c) => c.rtf).filter((x): x is number => x != null)) };
    }
    return out;
  }, [columns, rows, byCell]);

  const missingForActive = active ? rows.filter((s) => !byCell.has(cellKey(s.id, active.key))) : [];

  const playRow = (s: Sample) => {
    const items = columns
      .map((m) => byCell.get(cellKey(s.id, m.key))?.[0])
      .filter((c): c is Clip => !!c)
      .map((c) => ({ id: c.id, url: api.audioUrl(c) }));
    player.playSequence(items);
  };

  return (
    <section className="panel grid-panel">
      <header className="panel__head toolbar">
        <h2>Compare</h2>
        <input
          className="input input--search"
          placeholder="Filter by label, prompt or text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className="check small">
          <input type="checkbox" checked={onlyComplete} onChange={(e) => setOnlyComplete(e.target.checked)} />
          Only rows with every shown model
        </label>
        <div className="toolbar__spacer" />
        {fill ? (
          <button className="btn btn--sm" onClick={props.onCancelFill}>
            Generating {fill.done}/{fill.total} · Cancel
          </button>
        ) : (
          <button
            className="btn btn--primary btn--sm"
            disabled={!active || missingForActive.length === 0}
            title={active ? `Generate every missing clip in view with ${active.name}` : "Start a model first"}
            onClick={() => props.onFillMissing(missingForActive)}
          >
            Fill {missingForActive.length} missing{active ? ` · ${active.name}` : ""}
          </button>
        )}
        <a className="btn btn--ghost btn--sm" href={api.exportUrl}>
          Export CSV
        </a>
      </header>

      <div className="chips">
        <span className="muted small">Columns:</span>
        {models.map((m) => (
          <button
            key={m.key}
            className={`chip${hidden.has(m.key) ? "" : " chip--on"}`}
            onClick={() => props.onToggleModel(m.key)}
          >
            <span className={`dot dot--${m.state}`} />
            {m.name}
            <span className="muted"> {m.library.clips}</span>
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <p className="empty">
          {samples.length === 0
            ? "No samples yet. Add one above, or import a benchmark suite."
            : "No samples match the filter."}
        </p>
      ) : (
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th className="grid__sample">Sample ({rows.length})</th>
                {columns.map((m) => (
                  <th key={m.key} className={m.state === "ready" ? "is-active" : ""}>
                    <div className="th__name">
                      <span className={`dot dot--${m.state}`} />
                      {m.name}
                    </div>
                    <div className="muted small">
                      {stats[m.key].covered}/{rows.length} · mean RTF {stats[m.key].rtf?.toFixed(2) ?? "-"}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => {
                const rowPlaying = columns.some((m) => {
                  const c = byCell.get(cellKey(s.id, m.key))?.[0];
                  return c && (p.currentId === c.id || p.queue.includes(c.id));
                });
                return (
                  <tr key={s.id}>
                    <td className="grid__sample">
                      <input
                        className="label-input"
                        defaultValue={s.label ?? ""}
                        placeholder="+ add label"
                        onBlur={(e) => e.target.value !== (s.label ?? "") && props.onLabel(s, e.target.value)}
                      />
                      <div className="sample__prompt" title="Voice prompt">
                        {s.prompt || <span className="muted">(no voice prompt)</span>}
                      </div>
                      <div className="sample__text" title="Text">
                        “{s.text}”
                      </div>
                      <div className="sample__actions">
                        <button
                          className="btn btn--ghost btn--sm"
                          onClick={() => (rowPlaying && p.playing ? player.stop() : playRow(s))}
                          title="Play the latest clip of every shown model in order"
                        >
                          {rowPlaying && p.playing ? "■ Stop" : "▶ Play row"}
                        </button>
                        <button
                          className="btn btn--ghost btn--sm btn--danger"
                          onClick={() => props.onDeleteSample(s)}
                          title="Delete sample and all its clips"
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                    {columns.map((m) => {
                      const k = cellKey(s.id, m.key);
                      const clips = byCell.get(k) ?? [];
                      const open = expanded.has(k);
                      const shown = open ? clips : clips.slice(0, 1);
                      const isBusy = busy.has(k);
                      const canGen = m.state === "ready";
                      return (
                        <td key={m.key} className={`cell${canGen ? " is-active" : ""}`}>
                          {shown.map((c, i) => (
                            <ClipPlayer key={c.id} clip={c} latest={i === 0} onDelete={props.onDeleteClip} />
                          ))}
                          <div className="cell__foot">
                            {clips.length > 1 && (
                              <button
                                className="link small"
                                onClick={() =>
                                  setExpanded((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(k)) next.delete(k);
                                    else next.add(k);
                                    return next;
                                  })
                                }
                              >
                                {open ? "Show latest only" : `+${clips.length - 1} earlier`}
                              </button>
                            )}
                            {isBusy ? (
                              <span className="spinner small cell__gen">Generating…</span>
                            ) : canGen ? (
                              <button className="link small cell__gen" onClick={() => props.onGenerate(s, m.key)}>
                                {clips.length ? "↻ Generate again" : "Generate"}
                              </button>
                            ) : clips.length === 0 ? (
                              <span className="muted small">—</span>
                            ) : null}
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
