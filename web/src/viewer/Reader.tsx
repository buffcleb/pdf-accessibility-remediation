import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { TagEdit, TagMap } from "../api";
import { LiveRegion, errorMessage } from "../components/ui";
import { expand } from "./made";
import { loadTagmap } from "./pdf";
import { chunks, speechScript, startFor, type Utterance } from "./speech";

type Side = "left" | "right";

type Props = {
  jobId: string;
  versions: Record<Side, { number: number; label: string } | null>;
  ops: TagEdit[]; // pending edits, heard when reading the right-hand version
  selected: { side: Side; i: number } | null;
  // outline the tag being read and bring it into view (y: its top on the page, in page units)
  onShow: (side: Side, el: number, page: number, y: number) => void;
  onClose: () => void;
  // "Read aloud from here": start reading the right-hand version at this tag, at once (n tells one request
  // from the next for the same tag)
  from?: { i: number; n: number } | null;
};

const RATES = [0.75, 1, 1.25, 1.5, 1.75, 2];

function pickVoice(voices: SpeechSynthesisVoice[], lang?: string): SpeechSynthesisVoice | undefined {
  if (!lang) return undefined;
  const l = lang.toLowerCase();
  return voices.find((v) => v.lang.toLowerCase() === l)
    ?? voices.find((v) => v.lang.toLowerCase().split(/[-_]/)[0] === l.split("-")[0]);
}

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));

/** Reads a version's tags aloud in reading order with the browser's own voices, outlining each tag as it's read. */
export function Reader({ jobId, versions, ops, selected, onShow, onClose, from = null }: Props) {
  const speech = typeof window !== "undefined" && "speechSynthesis" in window ? window.speechSynthesis : null;
  const [side, setSide] = useState<Side>(versions.right ? "right" : "left");
  const [script, setScript] = useState<Utterance[] | null>(null);
  const [map, setMap] = useState<TagMap | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [at, setAt] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState<number>(() => {
    try { return Number(localStorage.getItem("pv-read-rate")) || 1; } catch { return 1; }
  });
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [announce, setAnnounce] = useState("");
  const token = useRef(0); // changes whenever reading stops or jumps, so a finished utterance from before is ignored
  const version = versions[side];
  const sideOps = side === "right" ? ops : [];

  useEffect(() => {
    if (!speech) return;
    const load = () => setVoices(speech.getVoices());
    load();
    speech.addEventListener("voiceschanged", load);
    return () => speech.removeEventListener("voiceschanged", load);
  }, [speech]);

  const stop = useCallback(() => {
    token.current++;
    speech?.cancel();
    setPlaying(false);
  }, [speech]);
  useEffect(() => () => { token.current++; speech?.cancel(); }, [speech]);

  // The words to read: the chosen version, with the pending edits when it's the one being edited
  useEffect(() => {
    if (!version) return;
    let live = true;
    setError(null);
    loadTagmap(jobId, version.number).then(
      // The tags the changes make are read as tags (made.ts, #692)
      (tagmap) => { if (!live) return; const d = expand(tagmap, sideOps); setMap(d.map); setScript(speechScript(d.map, d.ops)); },
      (e) => { if (live) setError(e); },
    );
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, version?.number, side === "right" ? ops : null]);

  const show = useCallback((u: Utterance) => {
    const box = map?.elements[u.el]?.boxes.find((b) => b.page === u.page);
    onShow(side, u.el, u.page, box ? box.bbox[1] : 0);
  }, [map, onShow, side]);

  const total = script?.length ?? 0;
  const current = script?.[Math.min(at, total - 1)];

  const speakFrom = useCallback((start: number) => {
    if (!speech || !script || !script.length) return;
    speech.cancel();
    const mine = ++token.current;
    const sayItem = (i: number) => {
      if (mine !== token.current) return;
      if (i >= script.length) {
        setPlaying(false);
        setAnnounce("Finished reading.");
        return;
      }
      setAt(i);
      const u = script[i];
      show(u);
      const parts = chunks(u.text);
      const sayPart = (n: number) => {
        if (mine !== token.current) return;
        if (n >= parts.length) { sayItem(i + 1); return; }
        const utt = new SpeechSynthesisUtterance(parts[n]);
        utt.rate = rate;
        if (u.lang) utt.lang = u.lang;
        const voice = pickVoice(voices, u.lang);
        if (voice) utt.voice = voice;
        utt.onend = () => sayPart(n + 1);
        utt.onerror = (e) => {
          // "interrupted"/"canceled" come from our own stop; anything else ends reading
          if (mine !== token.current || e.error === "interrupted" || e.error === "canceled") return;
          setPlaying(false);
          setAnnounce(`The browser's voice stopped: ${e.error}.`);
        };
        speech.speak(utt);
      };
      sayPart(0);
    };
    setPlaying(true);
    sayItem(start);
  }, [speech, script, rate, voices, show]);

  // A new rate or a changed script takes effect from the current tag
  const restartRef = useRef(speakFrom);
  restartRef.current = speakFrom;
  useEffect(() => {
    if (playing) restartRef.current(at);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rate, script]);
  useEffect(() => { if (at >= total && total) setAt(total - 1); }, [at, total]);

  // Read from a chosen tag as soon as its version's words are ready
  const started = useRef(0);
  useEffect(() => {
    if (!from || started.current === from.n || !script || !map) return;
    if (side !== "right") { setSide("right"); setScript(null); return; }  // "from here" is a tag of the right-hand version
    started.current = from.n;
    const n = startFor(script, map, from.i);
    if (n < 0) { setAnnounce("Nothing in the selected tag is read (it has no content, or is decorative)."); return; }
    setAt(n);
    setAnnounce("Reading from the selected tag.");
    speakFrom(n);
  }, [from, script, map, side, speakFrom]);

  const play = () => { setAnnounce("Reading."); speakFrom(at); };
  const pause = () => { stop(); setAnnounce(`Paused at tag ${at + 1} of ${total}.`); };
  const go = (i: number) => {
    if (!script?.length) return;
    const n = Math.max(0, Math.min(script.length - 1, i));
    setAt(n);
    if (playing) speakFrom(n);
    else {
      show(script[n]);
      setAnnounce(`Tag ${n + 1} of ${total}: ${script[n].text.slice(0, 120)}`);
    }
  };
  const fromSelected = () => {
    if (!script || !selected || selected.side !== side) return;
    const n = script.findIndex((u) => u.el === selected.i);
    if (n === -1) { setAnnounce("The selected tag isn't read (it has no content of its own, or is decorative)."); return; }
    go(n);
  };

  // J previous, K play or pause, L next (not while typing in a field)
  const keys = useRef<(e: KeyboardEvent) => void>(() => {});
  keys.current = (e: KeyboardEvent) => {
    if (e.altKey || e.ctrlKey || e.metaKey || typing(e.target)) return;
    const k = e.key.toLowerCase();
    if (k === "k") { if (playing) pause(); else play(); }
    else if (k === "j") go(at - 1);
    else if (k === "l") go(at + 1);
    else return;
    e.preventDefault();
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const choose = (s: Side) => { stop(); setSide(s); setAt(0); setScript(null); };
  const canSelected = !!selected && selected.side === side;
  const languages = useMemo(() => [...new Set((script ?? []).map((u) => u.lang).filter(Boolean))] as string[], [script]);
  const missingVoices = languages.filter((l) => !pickVoice(voices, l));

  return (
    <section className="pv-reader" aria-labelledby="reader-h">
      <div className="pv-reader-row">
        <h2 id="reader-h" className="pv-reader-title">Read aloud</h2>
        {!speech ? (
          <p className="small">This browser can't read aloud (it has no speech voices).</p>
        ) : (
          <>
            <label htmlFor="reader-side" className="small">Version</label>
            <select id="reader-side" value={side} onChange={(e) => choose(e.target.value as Side)}>
              {versions.left && <option value="left">Left: {versions.left.label}</option>}
              {versions.right && <option value="right">Right: {versions.right.label}{ops.length ? " (with unsaved changes)" : ""}</option>}
            </select>
            <div className="pv-reader-btns" role="group" aria-label="Reading controls">
              <button className="btn secondary small" onClick={() => go(at - 1)} disabled={!total || at === 0}>
                ⏮<span className="visually-hidden">Previous tag</span>
              </button>
              {playing ? (
                <button className="btn small" onClick={pause}>Pause</button>
              ) : (
                <button className="btn small" onClick={play} disabled={!total}>{at > 0 ? "Resume" : "Play"}</button>
              )}
              <button className="btn secondary small" onClick={() => go(at + 1)} disabled={!total || at >= total - 1}>
                ⏭<span className="visually-hidden">Next tag</span>
              </button>
              <button className="btn secondary small" onClick={fromSelected} disabled={!canSelected}>Go to the selected tag</button>
            </div>
            <label htmlFor="reader-rate" className="small">Speed</label>
            <select id="reader-rate" value={rate} onChange={(e) => {
              const r = Number(e.target.value);
              setRate(r);
              try { localStorage.setItem("pv-read-rate", String(r)); } catch { /* per-viewer preference only */ }
            }}>
              {RATES.map((r) => <option key={r} value={r}>{r === 1 ? "Normal" : `${r}×`}</option>)}
            </select>
          </>
        )}
        <button className="linkbtn small pv-reader-close" onClick={() => { stop(); onClose(); }}>Close<span className="visually-hidden"> read aloud</span></button>
      </div>
      {speech && (
        <>
          {error ? <p className="small" role="alert">{errorMessage(error)}</p>
            : !script ? <p className="small muted">Loading…</p>
            : !total ? <p className="small muted">This version has no tags to read.</p>
            : current && (
              <p className="small pv-reader-now">
                <span className="muted">{at + 1} of {total} · page {current.page}{current.lang ? ` · ${current.lang}` : ""}:</span>{" "}
                {current.text.slice(0, 200)}{current.text.length > 200 ? "…" : ""}
              </p>
            )}
          <details className="small muted pv-reader-about">
            <summary>Keys: <kbd>K</kbd> play or pause, <kbd>J</kbd> previous, <kbd>L</kbd> next</summary>
            A preview of the reading order and
            alt text, not a screen reader: NVDA, JAWS and VoiceOver word things differently and offer more ways to move
            around, so check with one before you finish. Voices depend on your browser and system.
            {side === "right" && ops.some((o) => o.op === "assign" || o.op === "tag_artifacts" || o.op === "regroup") && " Area changes are heard once saved."}
            {missingVoices.length > 0 && ` No voice for ${missingVoices.join(", ")} here: it's read with the default voice.`}
          </details>
        </>
      )}
      <LiveRegion message={announce} />
    </section>
  );
}
