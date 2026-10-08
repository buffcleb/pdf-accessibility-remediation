import { useEffect, useRef, useState, type DragEvent } from "react";
import type { DocVersion, Finding, TagEdit, TagElement, TagMap } from "../api";
import { ROLE_GROUPS, ROLE_NAMES, absorbedBy, addEdit, describe, overrides, pageOrder } from "./draft";
import { actionNote, checkElementRole } from "./roles";

type Props = {
  map: TagMap;
  element: TagElement | null;
  findings: (Finding & { extra?: { suggested_alt?: string } })[];
  page: number;
  ops: TagEdit[];
  setOps: (ops: TagEdit[]) => void;
  onSelect: (i: number) => void;
  canEdit: boolean;
  readOnlyReason: string;
  baseLabel: string;
  saving: boolean;
  onSave: (note: string, review: "none" | "sampled" | "full") => void;
  version: DocVersion;
  onReview: (full: boolean) => void;
  allowFullReview: boolean;
  reviewBusy: boolean;
  jump: number; // changes when a tag is picked in the tree: show the tag at the top of the panel
};

export function EditPanel({ map, element, findings, page, ops, setOps, onSelect, canEdit, readOnlyReason, baseLabel, saving,
  onSave, version, onReview, allowFullReview, reviewBusy, jump }: Props) {
  const [note, setNote] = useState("");
  const [review, setReview] = useState<"none" | "sampled" | "full">("none");
  const [tab, setTab] = useState<"tag" | "changes">("tag");
  const ref = useRef<HTMLElement>(null);
  const changes = canEdit && ops.length > 0;
  const showing = changes ? tab : "tag";

  useEffect(() => { if (!ops.length) setTab("tag"); }, [ops.length]);
  useEffect(() => {
    if (!jump) return;
    setTab("tag");
    const el = ref.current;
    if (!el) return;
    el.scrollTop = 0;
    const r = el.getBoundingClientRect();
    if (r.top < 0 || r.top > window.innerHeight) el.scrollIntoView({ block: "start" });
  }, [jump]);

  return (
    <aside className="pv-side" aria-label="Tag editor" ref={ref}>
      {!canEdit && (
        <p className="notice info small">{readOnlyReason}</p>
      )}
      {(version.kind === "reviewed" || version.kind === "corrected") && <ReviewStatus version={version} canEdit={canEdit} busy={reviewBusy} onReview={onReview}
        allowFull={allowFullReview} />}
      {changes && (
        <div className="pv-tabs" role="tablist" aria-label="Tag editor">
          <button role="tab" id="pv-tab-tag" aria-selected={showing === "tag"} aria-controls="pv-panel"
            className={showing === "tag" ? "active" : ""} onClick={() => setTab("tag")}>Tag</button>
          <button role="tab" id="pv-tab-changes" aria-selected={showing === "changes"} aria-controls="pv-panel"
            className={showing === "changes" ? "active" : ""} onClick={() => setTab("changes")}>
            Unsaved changes ({ops.length})
          </button>
          {showing === "tag" && (
            <button className="btn small pv-tab-save" onClick={() => setTab("changes")}>Save…</button>
          )}
        </div>
      )}
      <div id="pv-panel" role={changes ? "tabpanel" : undefined}
        aria-labelledby={changes ? (showing === "tag" ? "pv-tab-tag" : "pv-tab-changes") : undefined}
        className="pv-panel">
        {showing === "changes" ? (
          <section className="pv-section" aria-labelledby="pending-h">
            <h3 id="pending-h">Changes not saved yet ({ops.length})</h3>
            <ol className="pv-pending">
              {ops.map((op, n) => (
                <li key={n}>
                  <span className="small">{describe(op, map)}</span>{" "}
                  <button className="linkbtn small" onClick={() => setOps(ops.filter((_, k) => k !== n))}>
                    Undo<span className="visually-hidden"> this change</span>
                  </button>
                </li>
              ))}
            </ol>
            <div className="field">
              <label htmlFor="save-note">Note <span className="hint">(optional, e.g. what you checked)</span></label>
              <textarea id="save-note" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
            </div>
            <fieldset className="choices">
              <legend className="small">AI review of the saved version <span className="hint">(you can also run it later)</span></legend>
              <label className="check">
                <input type="radio" name="save-review" checked={review === "none"} onChange={() => setReview("none")} />
                <span>None now <span className="muted small">(only alt text you wrote is checked)</span></span>
              </label>
              <label className="check">
                <input type="radio" name="save-review" checked={review === "sampled"} onChange={() => setReview("sampled")} />
                <span>Flagged figures plus a sample</span>
              </label>
              {allowFullReview && (
                <label className="check">
                  <input type="radio" name="save-review" checked={review === "full"} onChange={() => setReview("full")} />
                  <span>Every figure <span className="muted small">(slower, uses more AI)</span></span>
                </label>
              )}
            </fieldset>
            <div className="actions">
              <button className="btn" disabled={saving} onClick={() => onSave(note, review)}>
                {saving ? "Saving…" : "Save as a new reviewed version"}
              </button>
              <button className="btn secondary" disabled={saving} onClick={() => setOps([])}>Discard all</button>
            </div>
            <p className="muted small">Saving re-checks the tags, runs veraPDF and confirms the pages look the same. {baseLabel} isn't changed.</p>
          </section>
        ) : (
          <>
            {element ? (
              <>
                <TagForm key={`${element.i}-${ops.length}`} map={map} element={element} findings={findings} ops={ops}
                  setOps={setOps} canEdit={canEdit} />
                {canEdit && <MergeSplit map={map} element={element} ops={ops} setOps={setOps} onSelect={onSelect} />}
              </>
            ) : (
              <p className="muted small">Select a tag on the right-hand page, or in the tag tree, to see or change it. Flagged tags are outlined in color.</p>
            )}
            <ReadingOrder map={map} page={page} ops={ops} setOps={setOps} selected={element?.i ?? null} onSelect={onSelect}
              canEdit={canEdit} />
          </>
        )}
      </div>
    </aside>
  );
}

function ReviewStatus({ version, canEdit, busy, onReview, allowFull }: {
  version: DocVersion; canEdit: boolean; busy: boolean; onReview: (full: boolean) => void; allowFull: boolean;
}) {
  const st = version.review_status;
  const when = version.reviewed_at ? new Date(version.reviewed_at).toLocaleString() : null;
  return (
    <section className="pv-review small" aria-live="polite">
      {st === "queued" || st === "running" ? (
        <p><strong>AI review running.</strong> The findings for {version.label} update when it finishes.</p>
      ) : st === "done" ? (
        <p>AI review ({version.review === "full" ? "every figure" : "flagged items and a sample"}) done{when ? ` ${when}` : ""}.</p>
      ) : st === "failed" ? (
        <p className="pv-review-failed">{version.review_error ?? "The AI review didn't finish."}</p>
      ) : (
        <p className="muted">The AI review hasn't been run on {version.label}. Only alt text you wrote was checked.</p>
      )}
      {canEdit && st !== "queued" && st !== "running" && version.status === "ready" && (
        <div className="actions">
          <button className="btn secondary small" disabled={busy} onClick={() => onReview(false)}>
            {st === "done" || st === "failed" ? "Run the AI review again" : "Run the AI review"}
          </button>
          {allowFull && (
            <button className="btn secondary small" disabled={busy} onClick={() => onReview(true)}>Review every figure</button>
          )}
        </div>
      )}
    </section>
  );
}

function TagForm({ map, element, findings, ops, setOps, canEdit }: {
  map: TagMap; element: TagElement; findings: Props["findings"]; ops: TagEdit[]; setOps: (o: TagEdit[]) => void; canEdit: boolean;
}) {
  const o = overrides(ops).get(element.i) ?? {};
  const role0 = o.role ?? element.role;
  const [role, setRole] = useState(role0);
  const [alt, setAlt] = useState(o.alt ?? element.alt ?? "");
  const actual0 = o.actual ?? element.actual_text ?? "";
  const [actual, setActual] = useState(actual0);
  const [lang, setLang] = useState(o.lang ?? element.lang ?? "");
  const [scope, setScope] = useState(o.scope ?? element.scope ?? "Column");
  const [decorative, setDecorative] = useState(!!o.decorative);
  useEffect(() => setRole(role0), [role0]);
  // Show alt text as soon as Figure or Formula is picked, so a formula can be retyped and described in one go
  const isFigure = [role, role0, element.role].some((r) => r === "Figure" || r === "Formula");
  const isCell = ["TH", "TD"].includes(role) || ["TH", "TD"].includes(element.role);
  const suggested = findings.map((f) => f.extra?.suggested_alt).find(Boolean);
  // Text a screen reader reads: the read-aloud text (ActualText) when there is one, otherwise the drawn text
  const readable = element.own && !isFigure;
  const base = { element: element.i, expect_role: element.role };

  function apply() {
    let next = ops;
    if (decorative && !o.decorative) {
      next = addEdit(next, { op: "set_decorative", ...base });
      setOps(next);
      return;
    }
    if (isCell && (role === "TH" || role === "TD") && (role !== role0 || (role === "TH" && scope !== (o.scope ?? element.scope)))) {
      next = addEdit(next, { op: "set_cell", ...base, role, ...(role === "TH" ? { scope } : {}) });
    } else if (role !== role0) {
      next = addEdit(next, { op: "set_role", ...base, role });
    }
    if (isFigure && alt !== (o.alt ?? element.alt ?? "")) next = addEdit(next, { op: "set_alt", ...base, alt });
    if (!isFigure && readable && actual !== actual0) next = addEdit(next, { op: "set_actual_text", ...base, text: actual });
    if (lang !== (o.lang ?? element.lang ?? "")) next = addEdit(next, { op: "set_lang", ...base, lang: lang.trim() });
    setOps(next);
  }

  const langOk = !lang || /^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$/.test(lang);
  // Only changes that make a valid tree; some reshape the tags around it (taking a list apart, wrapping text)
  const verdict = isCell ? { ok: true, action: "none" as const, reason: "" } : checkElementRole(map, element, role);
  const note = verdict.ok ? actionNote(verdict, role) : null;
  return (
    <section className="pv-section" aria-labelledby="tag-h">
      <h3 id="tag-h">{ROLE_NAMES[role0] ?? role0} <span className="mono muted small">{role0}</span></h3>
      <p className="small muted">Page {element.page ?? "–"}{element.raw_role ? ` · originally tagged ${element.raw_role}` : ""}</p>
      {actual0 && !isFigure ? (
        <>
          <p className="small"><span className="muted">Read aloud as:</span> “{actual0.slice(0, 300)}{actual0.length > 300 ? "…" : ""}”</p>
          {element.text && <p className="small muted">Drawn text, in drawing order: “{element.text.slice(0, 200)}{element.text.length > 200 ? "…" : ""}”</p>}
        </>
      ) : element.text && <p className="small">“{element.text.slice(0, 200)}{element.text.length > 200 ? "…" : ""}”</p>}
      {findings.map((f, n) => (
        <div key={n} className={`pv-finding sev-${f.severity}`}>
          <div className="small"><strong>{f.issue}</strong></div>
          {f.detail && <div className="small">{f.detail}</div>}
          {f.action && <div className="small muted">{f.action}</div>}
        </div>
      ))}
      <fieldset disabled={!canEdit || decorative} className="pv-fields">
        <div className="field">
          <label htmlFor="ed-role">Tag type</label>
          <select id="ed-role" value={role} onChange={(e) => setRole(e.target.value)}>
            {ROLE_GROUPS.map((g) => (
              <optgroup key={g.label} label={g.label}>
                {g.roles.map((r) => {
                  const v = isCell || r === element.role ? null : checkElementRole(map, element, r);
                  return (
                    <option key={r} value={r} disabled={!!v && !v.ok} title={v && !v.ok ? v.reason : undefined}>
                      {ROLE_NAMES[r] ? `${ROLE_NAMES[r]} (${r})` : r}{v && !v.ok ? " — not possible here" : ""}
                    </option>
                  );
                })}
              </optgroup>
            ))}
          </select>
        </div>
        {!verdict.ok && <p className="small pv-review-failed" role="alert">{verdict.reason}</p>}
        {note && <p className="small notice info">{note}</p>}
        {isCell && role === "TH" && (
          <div className="field">
            <label htmlFor="ed-scope">This header describes</label>
            <select id="ed-scope" value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="Column">its column</option>
              <option value="Row">its row</option>
              <option value="Both">its row and column</option>
            </select>
          </div>
        )}
        {isFigure && (
          <div className="field">
            <label htmlFor="ed-alt">Alt text</label>
            <textarea id="ed-alt" value={alt} maxLength={4000} onChange={(e) => setAlt(e.target.value)} />
            {suggested && suggested !== alt && (
              <button type="button" className="linkbtn small" onClick={() => setAlt(suggested)}>Use the suggested alt text</button>
            )}
          </div>
        )}
        {readable && (
          <div className="field">
            <label htmlFor="ed-actual">Read aloud as <span className="hint">(optional; leave empty to read the drawn text)</span></label>
            <textarea id="ed-actual" value={actual} maxLength={4000} onChange={(e) => setActual(e.target.value)} />
            <span className="hint">Use this when the drawn text reads wrongly: math and symbols, or letters drawn out of order.</span>
            {!actual && element.text && (
              <button type="button" className="linkbtn small" onClick={() => setActual(element.text ?? "")}>Start from the drawn text</button>
            )}
          </div>
        )}
        <div className="field">
          <label htmlFor="ed-lang">Language <span className="hint">(only if different from the document, e.g. fr-CA)</span></label>
          <input id="ed-lang" type="text" value={lang} aria-invalid={!langOk} onChange={(e) => setLang(e.target.value)} />
        </div>
      </fieldset>
      {isFigure && canEdit && (
        <label className="check">
          <input type="checkbox" checked={decorative} onChange={(e) => setDecorative(e.target.checked)} />
          <span>Decorative: screen readers skip it (the tag is removed and its content becomes an artifact)</span>
        </label>
      )}
      {canEdit && (
        <div className="actions">
          <button className="btn small" disabled={!langOk || !verdict.ok} onClick={apply}>Add change</button>
        </div>
      )}
    </section>
  );
}

function ReadingOrder({ map, page, ops, setOps, selected, onSelect, canEdit }: {
  map: TagMap; page: number; ops: TagEdit[]; setOps: (o: TagEdit[]) => void; selected: number | null;
  onSelect: (i: number) => void; canEdit: boolean;
}) {
  const list = pageOrder(map, page, ops);
  const over = overrides(ops);
  const [drag, setDrag] = useState<number | null>(null);

  function move(el: TagElement, target: TagElement, position: "before" | "after") {
    setOps([...ops, { op: "move", element: el.i, expect_role: el.role, target: target.i, expect_target_role: target.role, position }]);
  }
  function onDrop(e: DragEvent, target: TagElement, idx: number) {
    e.preventDefault();
    if (drag === null) return;
    const from = list.findIndex((x) => x.i === drag);
    if (from === -1 || list[from].i === target.i) return;
    move(list[from], target, from < idx ? "after" : "before");
    setDrag(null);
  }

  return (
    <section className="pv-section" aria-labelledby="order-h">
      <h3 id="order-h">Reading order, page {page}</h3>
      {list.length === 0 ? <p className="muted small">No tagged content on this page.</p> : (
        <ol className="pv-order">
          {list.map((el, idx) => (
            <li key={el.i} className={selected === el.i ? "selected" : ""} draggable={canEdit}
              onDragStart={() => setDrag(el.i)} onDragOver={(e) => e.preventDefault()} onDrop={(e) => onDrop(e, el, idx)}>
              <button className="linkbtn pv-order-item" onClick={() => onSelect(el.i)}>
                <span className="mono small">{over.get(el.i)?.role ?? el.role}</span>{" "}
                <span className="small">{(el.text ?? el.alt ?? "").slice(0, 70) || "(no text)"}</span>
              </button>
              {canEdit && (
                <span className="pv-order-btns">
                  <button className="btn secondary small" disabled={idx === 0} aria-label={`Move up: ${(el.text ?? el.role).slice(0, 40)}`}
                    onClick={() => move(el, list[idx - 1], "before")}>↑</button>
                  <button className="btn secondary small" disabled={idx === list.length - 1} aria-label={`Move down: ${(el.text ?? el.role).slice(0, 40)}`}
                    onClick={() => move(el, list[idx + 1], "after")}>↓</button>
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
      {canEdit && list.length > 1 && <p className="muted small">Drag items, or use the arrows, to change the order screen readers follow.</p>}
    </section>
  );
}

function MergeSplit({ map, element, ops, setOps, onSelect }: {
  map: TagMap; element: TagElement; ops: TagEdit[]; setOps: (o: TagEdit[]) => void; onSelect: (i: number) => void;
}) {
  const page = element.page ?? element.boxes[0]?.page ?? 1;
  const list = pageOrder(map, page, ops);
  const idx = list.findIndex((e) => e.i === element.i);
  const prev = idx > 0 ? list[idx - 1] : null;
  const next = idx >= 0 && idx < list.length - 1 ? list[idx + 1] : null;
  const split = ops.some((o) => o.op === "split" && o.element === element.i);
  const parts = element.parts ?? [];
  const label = (e: TagElement) => `${e.role}${e.text ? ` “${e.text.slice(0, 30)}${e.text.length > 30 ? "…" : ""}”` : ""}`;

  function merge(into: TagElement, other: TagElement) {
    // Merging into a tag that already absorbed others just extends that merge
    const existing = ops.findIndex((o) => o.op === "merge" && o.element === into.i);
    if (existing >= 0) {
      const op = ops[existing] as Extract<TagEdit, { op: "merge" }>;
      const nextOps = ops.slice();
      nextOps[existing] = { ...op, absorb: [...op.absorb, other.i] };
      setOps(nextOps);
    } else {
      setOps([...ops, { op: "merge", element: into.i, expect_role: into.role, absorb: [other.i] }]);
    }
    onSelect(into.i);
  }

  const absorbed = absorbedBy(ops);
  return (
    <section className="pv-section" aria-labelledby="ms-h">
      <h3 id="ms-h">Merge or split</h3>
      {element.own ? (
        <>
          <p className="muted small">
            Merge joins neighboring tags into one (useful for a formula broken into pieces). The merged tag keeps the type
            of the first one; change it to Formula and add alt text afterwards if needed.
          </p>
          <div className="actions">
            <button className="btn secondary small" disabled={!prev || absorbed.has(element.i)}
              onClick={() => prev && merge(prev, element)}>
              Merge with the previous tag{prev ? <span className="visually-hidden">: {label(prev)}</span> : null}
            </button>
            <button className="btn secondary small" disabled={!next} onClick={() => next && merge(element, next)}>
              Merge with the next tag{next ? <span className="visually-hidden">: {label(next)}</span> : null}
            </button>
          </div>
          {next && <p className="muted small">Next: {label(next)}</p>}
        </>
      ) : (
        <p className="muted small">This tag only groups other tags; merge the tags inside it instead.</p>
      )}
      {parts.length >= 2 && !split && (
        <>
          <p className="muted small">Split moves everything from a chosen piece onward into a new tag of the same type, right after this one.</p>
          <ol className="pv-parts">
            {parts.map((p, n) => {
              const child = p.kind === "tag" && p.el != null ? map.elements[p.el] : null;
              const text = child ? `${child.role}${child.text ? ` “${child.text.slice(0, 40)}”` : ""}` : p.kind === "link" ? "(link)" : p.text ?? "(content)";
              return (
                <li key={p.k}>
                  {n > 0 && (
                    <button className="btn secondary small" aria-label={`Split before: ${text}`}
                      onClick={() => setOps([...ops, { op: "split", element: element.i, expect_role: element.role, at: p.k }])}>
                      Split here
                    </button>
                  )}
                  <span className="small">{text}</span>
                </li>
              );
            })}
          </ol>
        </>
      )}
      {!split && <SplitInText element={element} onSplit={(at, char) =>
        setOps([...ops, { op: "split", element: element.i, expect_role: element.role, at, char }])} />}
      {split && <p className="small">This tag will be split when you save.</p>}
    </section>
  );
}

/** Split anywhere in a piece of text: put the cursor where the new tag should start. */
function SplitInText({ element, onSplit }: { element: TagElement; onSplit: (at: number, char: number) => void }) {
  const pieces = element.parts
    ? element.parts.filter((p) => p.kind === "content" && p.text && !/^\((image|drawing)\)$/.test(p.text))
      .map((p) => ({ k: p.k, text: p.text ?? "" }))
    : element.own && element.kids.length === 0 && element.text ? [{ k: 0, text: element.text }] : [];
  const [k, setK] = useState(pieces[0]?.k ?? 0);
  const [pos, setPos] = useState<number | null>(null);
  if (!pieces.length) return null;
  const piece = pieces.find((p) => p.k === k) ?? pieces[0];
  const ok = pos !== null && pos > 0 && pos < piece.text.length && piece.text.slice(0, pos).trim() !== "" && piece.text.slice(pos).trim() !== "";
  const track = (e: { currentTarget: HTMLTextAreaElement }) => setPos(e.currentTarget.selectionStart);
  return (
    <details className="pv-split-text">
      <summary className="small">Split somewhere else in the text</summary>
      {pieces.length > 1 && (
        <div className="field">
          <label htmlFor="split-piece" className="small">Piece of text</label>
          <select id="split-piece" value={piece.k} onChange={(e) => { setK(Number(e.target.value)); setPos(null); }}>
            {pieces.map((p, n) => <option key={p.k} value={p.k}>{n + 1}: {p.text.slice(0, 40)}</option>)}
          </select>
        </div>
      )}
      <div className="field">
        <label htmlFor="split-text" className="small">Click where the new tag should start</label>
        <textarea id="split-text" readOnly value={piece.text} rows={Math.min(6, 2 + Math.floor(piece.text.length / 40))}
          onSelect={track} onClick={track} onKeyUp={track} />
        <span className="hint">The text is shown in the order it's drawn on the page. If the tag has read-aloud text, it's removed from both parts; set it again afterwards.</span>
      </div>
      {ok && (
        <p className="small">
          <span className="muted">This tag:</span> “…{piece.text.slice(Math.max(0, pos! - 25), pos!)}”{" "}
          <span className="muted">New tag:</span> “{piece.text.slice(pos!, pos! + 25)}…”
        </p>
      )}
      <div className="actions">
        <button className="btn secondary small" disabled={!ok} onClick={() => ok && onSplit(piece.k, pos!)}>Split at the cursor</button>
      </div>
    </details>
  );
}
