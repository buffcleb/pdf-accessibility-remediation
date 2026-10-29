// A review item's status (#366): checked, fixed or not an issue, kept by the item's key. Set on Needs review, and in
// the tag manager where the item is fixed (#663); both read and write the same marks.
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { MARK_LABELS, get, put, type Finding, type FindingMark, type FindingMarks, type MarkStatus } from "../api";
import { fmtDate } from "./ui";

/** What a mark needs of a review item. */
type Item = Pick<Finding, "key" | "issue" | "page">;

export type Marks = {
  marks: Map<string, FindingMark>;
  canMark: boolean;
  mark: (f: Item, status: MarkStatus | null, note?: string | null) => Promise<void>;
  error: unknown;
  setError: (e: unknown) => void;
  said: string; // the last change, for a live region
};

/** A document's marks, and a way to set one. */
export function useFindingMarks(jobId: string): Marks {
  const [marks, setMarks] = useState<Map<string, FindingMark>>(new Map());
  const [canMark, setCanMark] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [said, setSaid] = useState("");
  useEffect(() => {
    get<FindingMarks>(`/jobs/${jobId}/finding-marks`)
      .then((m) => { setMarks(new Map(m.marks.map((x) => [x.key, x]))); setCanMark(m.can_mark); })
      .catch(setError);
  }, [jobId]);
  const mark = useCallback(async (f: Item, status: MarkStatus | null, note?: string | null) => {
    if (!f.key) return;
    try {
      const m = await put<FindingMark | null>(`/jobs/${jobId}/finding-marks/${f.key}`,
        { status, note: note === undefined ? marks.get(f.key)?.note ?? null : note });
      setMarks((cur) => { const next = new Map(cur); if (m) next.set(f.key!, m); else next.delete(f.key!); return next; });
      setSaid(status ? `${f.issue}: ${MARK_LABELS[status]}.` : `${f.issue}: not looked at.`);
      setError(null);
    } catch (e) { setError(e); }
  }, [jobId, marks]);
  return { marks, canMark, mark, error, setError, said };
}

/** One review item's status: a choice for people who can edit the document, and a note once it's signed off. */
export function MarkControl({ f, mark, canMark, onMark }: { f: Item; mark?: FindingMark; canMark: boolean;
  onMark: (f: Item, status: MarkStatus | null, note?: string | null) => void }) {
  const [note, setNote] = useState(mark?.note ?? "");
  useEffect(() => setNote(mark?.note ?? ""), [mark?.note]);
  const who = mark ? `${MARK_LABELS[mark.status]} by ${mark.set_by_email}, ${fmtDate(mark.set_at)}${mark.note ? `: ${mark.note}` : ""}` : "";
  if (!canMark) return mark ? <span className="pill good review-mark" title={who}>{MARK_LABELS[mark.status]}</span> : null;
  return (
    <div className="review-mark">
      <label>
        <span className="visually-hidden">Status of {f.issue}{f.page != null ? ` on page ${f.page}` : ""}</span>
        <select value={mark?.status ?? ""} title={who || undefined}
          onChange={(e) => onMark(f, (e.target.value || null) as MarkStatus | null)}>
          <option value="">Not looked at</option>
          {(Object.keys(MARK_LABELS) as MarkStatus[]).map((s) => <option key={s} value={s}>{MARK_LABELS[s]}</option>)}
        </select>
      </label>
      {mark && (
        <label>
          <span className="visually-hidden">Note on {f.issue}</span>
          <input type="text" value={note} maxLength={1000} placeholder="Note (optional)"
            onChange={(e) => setNote(e.target.value)}
            onBlur={() => { if (note.trim() !== (mark.note ?? "")) onMark(f, mark.status, note.trim() || null); }} />
        </label>
      )}
    </div>
  );
}

/** The tag manager's marks, so an item's status can be shown wherever the item is without passing them down. */
export const MarksContext = createContext<Marks | null>(null);

/** An item's status from the marks in context; nothing outside one, or for an item without a key. */
export function ItemStatus({ f }: { f: Item }) {
  const m = useContext(MarksContext);
  if (!m || !f.key) return null;
  return <MarkControl f={f} mark={m.marks.get(f.key)} canMark={m.canMark} onMark={(...a) => void m.mark(...a)} />;
}
