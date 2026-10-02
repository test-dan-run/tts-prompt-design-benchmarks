import { api } from "../api";
import { player, usePlayer } from "../player";
import type { Clip } from "../types";
import { fmtSeconds, fmtTime } from "../format";

interface Props {
  clip: Clip;
  latest?: boolean;
  onDelete?: (clip: Clip) => void;
}

export function ClipPlayer({ clip, latest, onDelete }: Props) {
  const p = usePlayer();
  const active = p.currentId === clip.id;
  const playing = active && p.playing;
  const queued = p.queue.includes(clip.id);
  const details = [
    `Model: ${clip.model_name ?? clip.model} (${clip.hf_repo ?? "?"})`,
    `Backend image: ${clip.backend_image ?? "?"}`,
    `Created: ${new Date(clip.created_at).toLocaleString()}`,
    `Latency: ${fmtSeconds(clip.latency_s)}  Audio: ${fmtSeconds(clip.audio_duration_s)}  RTF: ${clip.rtf ?? "-"}`,
    `Sample rate: ${clip.sample_rate ?? "-"} Hz`,
    `Request: ${JSON.stringify(clip.request)}`,
  ].join("\n");

  return (
    <div className={`clip${active ? " clip--active" : ""}${queued ? " clip--queued" : ""}${latest ? "" : " clip--old"}`}>
      <button
        className="clip__play"
        onClick={() => player.toggle(clip.id, api.audioUrl(clip))}
        aria-label={playing ? "Pause" : "Play"}
      >
        {playing ? "❚❚" : "▶"}
      </button>
      <div
        className="clip__bar"
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          if (active) player.seek(clip.id, (e.clientX - r.left) / r.width);
          else player.toggle(clip.id, api.audioUrl(clip));
        }}
      >
        <div className="clip__fill" style={{ width: `${active ? p.progress * 100 : 0}%` }} />
        <span className="clip__meta">
          {fmtSeconds(clip.audio_duration_s)} · RTF {clip.rtf?.toFixed(2) ?? "-"} · {fmtTime(clip.created_at)}
        </span>
      </div>
      <a className="icon-btn" href={api.audioUrl(clip)} download title="Download">
        ⤓
      </a>
      <span className="icon-btn" title={details}>
        ⓘ
      </span>
      {onDelete && (
        <button className="icon-btn icon-btn--danger" title="Delete clip" onClick={() => onDelete(clip)}>
          ×
        </button>
      )}
    </div>
  );
}
