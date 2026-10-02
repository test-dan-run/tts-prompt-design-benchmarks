import { useState } from "react";
import type { ModelInfo, Suite } from "../types";

interface Props {
  active: ModelInfo | undefined;
  suites: Suite[];
  onAdd: (prompt: string, text: string, label: string, generate: boolean) => Promise<boolean>;
  onImportSuite: (name: string) => void;
}

export function Composer({ active, suites, onAdd, onImportSuite }: Props) {
  const [prompt, setPrompt] = useState("");
  const [text, setText] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (generate: boolean) => {
    if (!text.trim()) return;
    setBusy(true);
    const ok = await onAdd(prompt, text, label, generate);
    setBusy(false);
    if (ok) {
      setText("");
      setLabel("");
    }
  };

  return (
    <section className="panel composer">
      <header className="panel__head">
        <h2>New sample</h2>
        {suites.length > 0 && (
          <select
            className="select"
            value=""
            onChange={(e) => e.target.value && onImportSuite(e.target.value)}
            title="Add every case of a benchmark suite as samples"
          >
            <option value="">Import suite…</option>
            {suites.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name} ({s.cases})
              </option>
            ))}
          </select>
        )}
      </header>
      <label className="field">
        <span>Voice prompt</span>
        <textarea
          rows={3}
          value={prompt}
          placeholder="Describe the speaker: a calm, low-pitched British woman in her forties, slightly amused"
          onChange={(e) => setPrompt(e.target.value)}
        />
      </label>
      <label className="field">
        <span>Text</span>
        <textarea
          rows={3}
          value={text}
          placeholder="What should the speaker say?"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit(!!active);
          }}
        />
      </label>
      <div className="composer__row">
        <input className="input" value={label} placeholder="Label (optional)" onChange={(e) => setLabel(e.target.value)} />
        <button className="btn" disabled={busy || !text.trim()} onClick={() => submit(false)}>
          Add sample
        </button>
        <button
          className="btn btn--primary"
          disabled={busy || !text.trim() || !active}
          title={active ? "Ctrl/⌘ + Enter" : "Start a model first"}
          onClick={() => submit(true)}
        >
          {active ? `Add & generate with ${active.name}` : "No model running"}
        </button>
      </div>
    </section>
  );
}
