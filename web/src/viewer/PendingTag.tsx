// A tag pending edits make, chosen before it's saved (#427): its type and alt text, or taking it back
import { useEffect, useRef, useState } from "react";
import type { TagEdit } from "../api";
import type { NewTag } from "./areaPreview";
import { ROLE_NAMES } from "./draft";
import { needsAlt, pendingRoles } from "./pending";

const name = (r: string) => (ROLE_NAMES[r] ? `${ROLE_NAMES[r]} (${r})` : r);

/** The chosen new tag: what it is and holds, its type and (for a figure or formula) alt text to change, and the
 * other new tags to move to. Changes go into the edit that makes it, so undo and redo take them back as any edit. */
export function PendingTag({ tag, op, all, onChange, onRemove, onChoose, onClose }: {
  tag: NewTag; op: TagEdit; all: NewTag[];
  onChange: (role: string, alt: string) => boolean; onRemove: () => void; onChoose: (op: number) => void;
  onClose: () => void;
}) {
  const [role, setRole] = useState(tag.role);
  const [alt, setAlt] = useState(tag.alt);
  const typeRef = useRef<HTMLSelectElement>(null);
  const barRef = useRef<HTMLElement>(null);
  useEffect(() => { setRole(tag.role); setAlt(tag.alt); }, [tag.op, tag.role, tag.alt]);
  // Chosen (or just made): the next step, its type, is one key away
  useEffect(() => {
    barRef.current?.scrollIntoView({ block: "nearest" });
    typeRef.current?.focus({ preventScroll: true });
  }, [tag.op]);
  const missingAlt = needsAlt(role) && !alt.trim();
  const changed = role !== tag.role || (needsAlt(role) && alt.trim() !== tag.alt);
  const words = tag.alt || tag.text;
  return (
    <section className="pv-newtag" ref={barRef} aria-labelledby="pv-newtag-h"
      onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}>
      <h2 id="pv-newtag-h" className="pv-newtag-h">New tag <span className="small muted">(not saved)</span></h2>
      <span className="pv-newtag-text small" title={words}>{name(tag.role)}{words ? `: ${words}` : ""}</span>
      {all.length > 1 && (
        <label className="small">New tag
          <select value={tag.op} onChange={(e) => onChoose(Number(e.target.value))}>
            {all.map((t, n) => (
              <option key={t.op} value={t.op}>{n + 1}. {t.role}{t.text ? ` ${t.text.slice(0, 30)}` : ""}</option>
            ))}
          </select>
        </label>
      )}
      <form className="pv-newtag-form"
        onSubmit={(e) => { e.preventDefault(); if (changed && !missingAlt) onChange(role, alt); }}>
        <label className="small">Type
          <select ref={typeRef} value={role} onChange={(e) => setRole(e.target.value)}>
            {pendingRoles(op).map((r) => <option key={r} value={r}>{name(r)}</option>)}
          </select>
        </label>
        {needsAlt(role) && (
          <label className="small">Alt text
            <input value={alt} onChange={(e) => setAlt(e.target.value)} aria-invalid={missingAlt || undefined}
              aria-describedby={missingAlt ? "pv-newtag-alt" : undefined} />
          </label>
        )}
        {missingAlt && <span id="pv-newtag-alt" className="small muted">A {role.toLowerCase()} needs alt text.</span>}
        <button type="submit" className="btn small" disabled={!changed || missingAlt}>Change</button>
        <button type="button" className="btn secondary small" onClick={onRemove}>Take back</button>
        <button type="button" className="btn secondary small" onClick={onClose}
          aria-label="Close the new tag's details">Close</button>
      </form>
    </section>
  );
}
