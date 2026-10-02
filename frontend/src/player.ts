// One shared <audio> element for the whole app, so starting a clip stops the
// previous one, and a queue can play a row of clips back to back (A/B/C...).
import { useSyncExternalStore } from "react";

export interface PlayerState {
  currentId: string | null;
  playing: boolean;
  progress: number; // 0..1
  queue: string[]; // remaining clip ids after the current one
}

type Src = { id: string; url: string };

const audio = typeof Audio !== "undefined" ? new Audio() : null;
let state: PlayerState = { currentId: null, playing: false, progress: 0, queue: [] };
let queued: Src[] = [];
const listeners = new Set<() => void>();

function set(patch: Partial<PlayerState>) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}

function load(src: Src) {
  if (!audio) return;
  audio.src = src.url;
  set({ currentId: src.id, progress: 0, queue: queued.map((q) => q.id) });
  void audio.play().catch(() => set({ playing: false }));
}

if (audio) {
  audio.addEventListener("play", () => set({ playing: true }));
  audio.addEventListener("pause", () => set({ playing: false }));
  audio.addEventListener("timeupdate", () =>
    set({ progress: audio.duration ? audio.currentTime / audio.duration : 0 }),
  );
  audio.addEventListener("ended", () => {
    const next = queued.shift();
    if (next) load(next);
    else set({ playing: false, progress: 1, queue: [] });
  });
}

export const player = {
  /** Toggle a single clip. */
  toggle(id: string, url: string) {
    if (!audio) return;
    if (state.currentId === id) {
      if (audio.paused) void audio.play();
      else audio.pause();
      return;
    }
    queued = [];
    load({ id, url });
  },
  /** Play several clips one after another. */
  playSequence(items: Src[]) {
    if (!items.length) return;
    queued = items.slice(1);
    load(items[0]);
  },
  seek(id: string, fraction: number) {
    if (audio && state.currentId === id && audio.duration) audio.currentTime = fraction * audio.duration;
  },
  stop() {
    queued = [];
    audio?.pause();
    set({ currentId: null, playing: false, progress: 0, queue: [] });
  },
};

export function usePlayer(): PlayerState {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
  );
}
