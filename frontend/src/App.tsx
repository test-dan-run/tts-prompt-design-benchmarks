import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { Composer } from "./components/Composer";
import { CompareGrid } from "./components/CompareGrid";
import { ModelsPanel } from "./components/ModelsPanel";
import type { Clip, ModelInfo, Sample, Suite } from "./types";

const HIDDEN_KEY = "ttsbench.hiddenModels";

function loadHidden(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) ?? "[]"));
  } catch {
    return new Set();
  }
}

interface Toast {
  id: number;
  kind: "error" | "info";
  text: string;
}

export default function App() {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [suites, setSuites] = useState<Suite[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(loadHidden);
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [fill, setFill] = useState<{ done: number; total: number } | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [online, setOnline] = useState(true);
  const cancelFill = useRef(false);

  const toast = useCallback((text: string, kind: Toast["kind"] = "error") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 9000 : 4000);
  }, []);

  const refreshModels = useCallback(async () => {
    try {
      setModels(await api.models());
      setOnline(true);
    } catch {
      setOnline(false);
    }
  }, []);

  const refreshSamples = useCallback(async () => {
    try {
      setSamples(await api.samples());
    } catch {
      /* surfaced by the model poll */
    }
  }, []);

  // Poll quickly while a backend is loading, slowly otherwise. Samples are
  // re-read periodically so clips made by the CLI benchmark show up too.
  const starting = models.some((m) => m.state === "starting");
  useEffect(() => {
    void refreshModels();
    const t = setInterval(refreshModels, starting ? 2000 : 6000);
    return () => clearInterval(t);
  }, [refreshModels, starting]);
  useEffect(() => {
    void refreshSamples();
    api.suites().then(setSuites).catch(() => undefined);
    const t = setInterval(refreshSamples, 15000);
    return () => clearInterval(t);
  }, [refreshSamples]);

  useEffect(() => {
    try {
      localStorage.setItem(HIDDEN_KEY, JSON.stringify([...hidden]));
    } catch {
      /* storage unavailable */
    }
  }, [hidden]);

  const active = useMemo(() => models.find((m) => m.state === "ready"), [models]);
  const loading = useMemo(() => models.find((m) => m.state === "starting"), [models]);

  // Fold a freshly generated clip into local state without a full reload.
  const addClip = useCallback((clip: Clip) => {
    setSamples((prev) => prev.map((s) => (s.id === clip.sample_id ? { ...s, clips: [clip, ...s.clips] } : s)));
    setModels((prev) =>
      prev.map((m) => (m.key === clip.model ? { ...m, library: { ...m.library, clips: m.library.clips + 1 } } : m)),
    );
  }, []);

  const generate = useCallback(
    async (sample: Sample, model: string) => {
      const k = `${sample.id}:${model}`;
      setBusy((b) => new Set(b).add(k));
      try {
        addClip(await api.generate(sample.id, model));
        return true;
      } catch (e) {
        toast(`${model}: ${(e as Error).message}`);
        return false;
      } finally {
        setBusy((b) => {
          const n = new Set(b);
          n.delete(k);
          return n;
        });
      }
    },
    [addClip, toast],
  );

  const fillMissing = async (rows: Sample[]) => {
    if (!active) return;
    cancelFill.current = false;
    setFill({ done: 0, total: rows.length });
    // Sequential on purpose: one GPU, one backend, predictable latency numbers.
    for (let i = 0; i < rows.length; i++) {
      if (cancelFill.current) break;
      const ok = await generate(rows[i], active.key);
      if (!ok) break;
      setFill({ done: i + 1, total: rows.length });
    }
    setFill(null);
  };

  const startModel = async (m: ModelInfo) => {
    setPending((p) => ({ ...p, [m.key]: true }));
    try {
      await api.start(m.key);
      toast(`Loading ${m.name}. First start can take a few minutes.`, "info");
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setPending((p) => ({ ...p, [m.key]: false }));
      void refreshModels();
    }
  };

  const stopModel = async (m: ModelInfo) => {
    setPending((p) => ({ ...p, [m.key]: true }));
    try {
      await api.stop(m.key);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setPending((p) => ({ ...p, [m.key]: false }));
      void refreshModels();
    }
  };

  const addSample = async (prompt: string, text: string, label: string, gen: boolean) => {
    try {
      const s = await api.createSample(prompt, text, label);
      setSamples((prev) => (prev.some((x) => x.id === s.id) ? prev : [{ ...s, clips: [] }, ...prev]));
      if (gen && active) {
        const existing = samples.find((x) => x.id === s.id);
        await generate(existing ?? { ...s, clips: [] }, active.key);
      }
      return true;
    } catch (e) {
      toast((e as Error).message);
      return false;
    }
  };

  const importSuite = async (name: string) => {
    try {
      const added = await api.importSuite(name);
      toast(`Imported ${added.length} samples from ${name}`, "info");
      await refreshSamples();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const deleteClip = async (clip: Clip) => {
    if (!window.confirm(`Delete this ${clip.model_name ?? clip.model} clip?`)) return;
    try {
      await api.deleteClip(clip.id);
      setSamples((prev) =>
        prev.map((s) => (s.id === clip.sample_id ? { ...s, clips: s.clips.filter((c) => c.id !== clip.id) } : s)),
      );
      void refreshModels();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const deleteSample = async (s: Sample) => {
    if (!window.confirm(`Delete this sample and its ${s.clips.length} clip(s)?`)) return;
    try {
      await api.deleteSample(s.id);
      setSamples((prev) => prev.filter((x) => x.id !== s.id));
      void refreshModels();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const labelSample = async (s: Sample, label: string) => {
    try {
      await api.labelSample(s.id, label || null);
      setSamples((prev) => prev.map((x) => (x.id === s.id ? { ...x, label: label || null } : x)));
    } catch (e) {
      toast((e as Error).message);
    }
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <img src="/favicon.svg" alt="" width={22} height={22} />
          TTS Prompt-Design Bench
        </div>
        <div className="topbar__status small">
          {!online ? (
            <span className="warn">Controller unreachable</span>
          ) : active ? (
            <>
              <span className="dot dot--ready" /> {active.name} ready
            </>
          ) : loading ? (
            <>
              <span className="dot dot--starting" /> Loading {loading.name}…
            </>
          ) : (
            <span className="muted">No model running</span>
          )}
        </div>
      </header>

      <main className="layout">
        <aside className="layout__side">
          <ModelsPanel models={models} pending={pending} onStart={startModel} onStop={stopModel} />
          <Composer active={active} suites={suites} onAdd={addSample} onImportSuite={importSuite} />
        </aside>
        <div className="layout__main">
          <CompareGrid
            samples={samples}
            models={models}
            hidden={hidden}
            onToggleModel={(key) =>
              setHidden((h) => {
                const n = new Set(h);
                if (n.has(key)) n.delete(key);
                else n.add(key);
                return n;
              })
            }
            active={active}
            busy={busy}
            fill={fill}
            onGenerate={(s, m) => void generate(s, m)}
            onFillMissing={(rows) => void fillMissing(rows)}
            onCancelFill={() => (cancelFill.current = true)}
            onDeleteClip={deleteClip}
            onDeleteSample={deleteSample}
            onLabel={labelSample}
          />
        </div>
      </main>

      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast--${t.kind}`}>
            {t.text}
          </div>
        ))}
      </div>
    </div>
  );
}
