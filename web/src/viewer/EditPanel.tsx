import { useEffect, useId, useRef, useState } from "react";
import type { AreaGroup, AreaTags, DocVersion, Finding, TagEdit, TagElement, TagMap } from "../api";
import { ItemStatus } from "../components/reviewMarks";
import { TabList } from "../components/TabList";
import { areaTab, type AreaTab } from "./areaTabs";
import { ROLE_GROUPS, ROLE_NAMES, absorbedBy, addEdit, deletedTags, describe, draftTree, inside, overrides, pageOrder, pendingArea, type Area, type AreaBoxes } from "./draft";
import { CONTENT_ROLES, NEW_AREA_ROLES, actionNote, checkElementRole, checkMove } from "./roles";
import { splitPieces } from "./pieces";
import { deleteCheck, deleteMany, type DeleteChoice } from "./deleting";
import { headerChoices, headersOf } from "./cells";
import { ReadingPicker } from "./ReadingPicker";
import { choiceFor, readingsOf, type Readings } from "./readings";

type Props = {
  map: TagMap;
  element: TagElement | null;
  findings: (Finding & { extra?: { suggested_alt?: string } & Partial<Readings> })[];
  page: number;
  ops: TagEdit[];
  setOps: (ops: TagEdit[]) => void;
  onSelect: (i: number) => void;
  canEdit: boolean;
  readOnlyReason: string;
  baseLabel: string;
  saving: boolean;
  onSave: (note: string, review: "none" | "sampled" | "full", replace: boolean) => void;
  version: DocVersion;
  onReview?: (full: boolean) => void; // none: the document was made without AI, so it gets no AI review (#659)
  allowFullReview: boolean;
  reviewBusy: boolean;
  jump: number; // changes when a tag is picked in the tree: show the tag at the top of the panel
  area: Area | null;
  setArea: (a: Area | null) => void;
  areaMode: boolean;
  setAreaMode: (on: boolean) => void;
  count?: number; // how many tags are selected (the panel shows the first)
  onActions?: (from: HTMLElement) => void; // open the menu for the selection
  describe?: Describe; // "Describe with AI", where the person may use it
  suggest?: Suggest; // the AI's tags for an area, where the person may use it
  onBoxes?: (b: AreaBoxes | null) => void; // the AI's figures for an area, to draw on the page
};

/** "Describe with AI": the AI's description of a box on a page. */
export type Describe = (page: number, bbox: [number, number, number, number]) => Promise<string>;

/** The AI's tags for an area: its items of content grouped, in reading order. */
export type Suggest = (page: number, bbox: [number, number, number, number],
  items: { kind: string; text: string; bbox: [number, number, number, number] }[]) => Promise<AreaTags>;

export function EditPanel({ map, element, findings, page, ops, setOps, onSelect, canEdit, readOnlyReason, baseLabel, saving,
  onSave, version, onReview, allowFullReview, reviewBusy, jump, area, setArea, areaMode, setAreaMode, count = 1, onActions, describe, suggest, onBoxes }: Props) {
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
          <SaveChanges map={map} ops={ops} setOps={setOps} version={version} baseLabel={baseLabel} saving={saving} onSave={onSave} aiReview={!!onReview}
            allowFullReview={allowFullReview} prefix="save" />
        ) : (
          <>
            {element ? (
              <>
                {onActions && (
                  <div className="pv-actions-row">
                    <button type="button" className="btn secondary small" aria-haspopup="menu" onClick={(e) => onActions(e.currentTarget)}>
                      Actions<span aria-hidden="true"> ▾</span>
                    </button>
                    <span className="small muted" role="status">
                      {count > 1 ? `${count} tags selected. The first is shown here; Actions apply to all of them.`
                        : "Also: right-click a tag. Ctrl-click (Cmd-click on a Mac) or Shift-click selects several."}
                    </span>
                  </div>
                )}
                {ops.some((o) => o.op === "build_list" && o.element === element.i) && (
                  <p className="notice info small" role="note">This tag is being built into a list, shown in the tag tree as it will be
                    saved. Choose its new tags there to change them; to build the list again, undo it under Unsaved changes.</p>
                )}
                <TagForm key={`${element.i}-${ops.length}`} map={map} element={element} findings={findings} ops={ops}
                  setOps={setOps} canEdit={canEdit} onSelect={onSelect} />
                {canEdit && <MergeSplit map={map} element={element} ops={ops} setOps={setOps} onSelect={onSelect} />}
              </>
            ) : (
              <p className="muted small">Select a tag on the right-hand page, or in the tag tree, to see or change it. Flagged tags are outlined in color.</p>
            )}
            {canEdit && <AreaEdit map={map} page={page} element={element} ops={ops} setOps={setOps} area={area}
              setArea={setArea} areaMode={areaMode} setAreaMode={setAreaMode} onSelect={onSelect} describe={describe} suggest={suggest} onBoxes={onBoxes} />}
          </>
        )}
      </div>
    </aside>
  );
}

/** The changes not saved yet, each with Undo, and the form that saves them: a note, where to save, and whether to run
 * the AI review. In the panel's Unsaved changes tab and in the dialog the heading opens (`prefix` keeps their ids and
 * radio groups apart). */
export function SaveChanges({ map, ops, setOps, version, baseLabel, saving, onSave, allowFullReview, prefix, aiReview = true }: {
  map: TagMap; ops: TagEdit[]; setOps: (o: TagEdit[]) => void; version: DocVersion; baseLabel: string; saving: boolean;
  onSave: Props["onSave"]; allowFullReview: boolean; prefix: string;
  aiReview?: boolean; // false: the document was made without AI, so saves aren't AI-reviewed (#659)
}) {
  const [note, setNote] = useState("");
  // Editing a reviewed version: save into it (the usual thing while a long review goes on) or make a new one
  const canReplace = version.kind === "reviewed";
  const [into, setInto] = useState(true);
  const [review, setReview] = useState<"none" | "sampled" | "full">("none");
  return (
    <section className="pv-section" aria-labelledby={`${prefix}-h`}>
      <h3 id={`${prefix}-h`}>Changes not saved yet ({ops.length})</h3>
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
        <label htmlFor={`${prefix}-note`}>Note <span className="hint">(optional, e.g. what you checked)</span></label>
        <textarea id={`${prefix}-note`} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
      </div>
      {canReplace && (
        <fieldset className="choices">
          <legend className="small">Where to save</legend>
          <label className="check">
            <input type="radio" name={`${prefix}-where`} checked={into} onChange={() => setInto(true)} />
            <span>Into {baseLabel} <span className="muted small">(it keeps its name; the file as it is now is kept
              for 24 hours, so this save can be undone from the document's Versions)</span></span>
          </label>
          <label className="check">
            <input type="radio" name={`${prefix}-where`} checked={!into} onChange={() => setInto(false)} />
            <span>As a new reviewed version <span className="muted small">({baseLabel} stays as it is)</span></span>
          </label>
        </fieldset>
      )}
      {aiReview && <fieldset className="choices">
        <legend className="small">AI review of the saved version <span className="hint">(you can also run it later)</span></legend>
        <label className="check">
          <input type="radio" name={`${prefix}-review`} checked={review === "none"} onChange={() => setReview("none")} />
          <span>None now <span className="muted small">(only alt text you wrote is checked)</span></span>
        </label>
        <label className="check">
          <input type="radio" name={`${prefix}-review`} checked={review === "sampled"} onChange={() => setReview("sampled")} />
          <span>Flagged figures plus a sample</span>
        </label>
        {allowFullReview && (
          <label className="check">
            <input type="radio" name={`${prefix}-review`} checked={review === "full"} onChange={() => setReview("full")} />
            <span>Every figure <span className="muted small">(slower, uses more AI)</span></span>
          </label>
        )}
      </fieldset>}
      <div className="actions">
        <button className="btn" disabled={saving} onClick={() => onSave(note, review, canReplace && into)}>
          {saving ? "Saving…" : canReplace && into ? `Save into ${baseLabel}` : "Save as a new reviewed version"}
        </button>
        <button className="btn secondary" disabled={saving} onClick={() => setOps([])}>Discard all</button>
      </div>
      <p className="muted small">Saving re-checks the tags, runs veraPDF and confirms the pages look the same.
        {canReplace && into ? ` ${baseLabel} is replaced once the new file passes those checks; if it doesn't, it stays as it is.` : ` ${baseLabel} isn't changed.`}</p>
    </section>
  );
}

export function ReviewStatus({ version, canEdit, busy, onReview, allowFull }: {
  version: DocVersion; canEdit: boolean; busy: boolean; onReview?: (full: boolean) => void; allowFull: boolean;
}) {
  const st = version.review_status;
  const when = version.reviewed_at ? new Date(version.reviewed_at).toLocaleString() : null;
  if (!onReview) {
    return <section className="pv-review small"><p className="muted">Made without AI, so there's no AI review. Only alt
      text you wrote was checked.</p></section>;
  }
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

/** The selected tag: what it is and says, its findings, and its type, alt text, read-aloud text and language to
 * change. In the panel, and in the "Edit tag" dialog (`prefix` keeps the two forms' ids apart; `onDone` is told
 * after a change is added). */
export function TagForm({ map, element, findings, ops, setOps, canEdit, onSelect, prefix = "ed", onDone }: {
  map: TagMap; element: TagElement; findings: Props["findings"]; ops: TagEdit[]; setOps: (o: TagEdit[]) => void; canEdit: boolean;
  onSelect: (i: number) => void; prefix?: string; onDone?: () => void;
}) {
  const [deleting, setDeleting] = useState(false);
  const o = overrides(ops, map).get(element.i) ?? {};
  const role0 = o.role ?? element.role;
  const [role, setRole] = useState(role0);
  const [alt, setAlt] = useState(o.alt ?? element.alt ?? "");
  const actual0 = o.actual ?? element.actual_text ?? "";
  const [actual, setActual] = useState(actual0);
  const [lang, setLang] = useState(o.lang ?? element.lang ?? "");
  const [scope, setScope] = useState(o.scope ?? element.scope ?? "Column");
  const [decorative, setDecorative] = useState(!!o.decorative);
  // A form field's label: its tooltip, what a screen reader announces for it (#341)
  const isForm = element.role === "Form";
  const fieldLabel0 = o.fieldLabel ?? element.name ?? "";
  // A link's description: what a screen reader announces for it, kept on its annotation too (#364)
  const isLink = element.role === "Link" || element.role === "Annot";
  const linkText0 = o.alt ?? element.alt ?? element.name ?? "";
  const [linkText, setLinkText] = useState(linkText0);
  const [fieldLabel, setFieldLabel] = useState(fieldLabel0);
  // A cell's headers: the header cells of its table it's read with
  const named0 = headersOf(map, ops, element.i);
  const [named, setNamed] = useState(named0);
  useEffect(() => setRole(role0), [role0]);
  // Show alt text as soon as Figure or Formula is picked, so a formula can be retyped and described in one go
  const isFigure = [role, role0, element.role].some((r) => r === "Figure" || r === "Formula");
  const isCell = ["TH", "TD"].includes(role) || ["TH", "TD"].includes(element.role);
  const suggested = findings.map((f) => f.extra?.suggested_alt).find(Boolean);
  // A formula whose two readings disagree (#403): both offered, and the choice goes with the edit
  const readings = readingsOf(findings);
  const [picked, setPicked] = useState(false);
  // Text a screen reader reads: the read-aloud text (ActualText) when there is one, otherwise the drawn text
  const readable = element.own && !isFigure;
  const base = { element: element.i, expect_role: element.role };
  const choices = isCell ? headerChoices(map, ops, element.i) : [];

  function apply() {
    let next = ops;
    if (decorative && !o.decorative) {
      next = addEdit(next, { op: "set_decorative", ...base });
      setOps(next);
      onDone?.();
      return;
    }
    if (isCell && (role === "TH" || role === "TD") && (role !== role0 || (role === "TH" && scope !== (o.scope ?? element.scope)))) {
      next = addEdit(next, { op: "set_cell", ...base, role, ...(role === "TH" ? { scope } : {}) });
    } else if (role !== role0) {
      next = addEdit(next, { op: "set_role", ...base, role });
    }
    if (choices.length) {
      const chosen = choices.filter((h) => named.includes(h.i)).map((h) => h.i);  // in table order
      if (chosen.join() !== named0.join()) next = addEdit(next, { op: "set_headers", ...base, headers: chosen });
    }
    // A chosen reading is saved even when it's the one the tag already has: the choice settles the finding
    const reading = readings && (picked || (isFigure ? alt !== (o.alt ?? element.alt ?? "") : actual !== actual0))
      ? { reading: choiceFor(isFigure ? alt : actual, readings) } : {};
    if (isFigure && (alt !== (o.alt ?? element.alt ?? "") || (readings && picked))) {
      next = addEdit(next, { op: "set_alt", ...base, alt, ...reading });
    }
    if (!isFigure && readable && (actual !== actual0 || (readings && picked))) {
      next = addEdit(next, { op: "set_actual_text", ...base, text: actual, ...reading });
    }
    if (lang !== (o.lang ?? element.lang ?? "")) next = addEdit(next, { op: "set_lang", ...base, lang: lang.trim() });
    if (isLink && linkText.trim() && linkText.trim() !== linkText0.trim()) {
      next = addEdit(next, { op: "set_alt", ...base, alt: linkText.trim() });
    }
    if (isForm && fieldLabel.trim() && fieldLabel.trim() !== fieldLabel0.trim()) {
      next = addEdit(next, { op: "set_field_label", ...base, label: fieldLabel.trim() });
    }
    setOps(next);
    onDone?.();
  }

  const langOk = !lang || /^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$/.test(lang);
  // Only changes that make a valid tree; some reshape the tags around it (taking a list apart, wrapping text)
  const verdict = isCell ? { ok: true, action: "none" as const, reason: "" } : checkElementRole(map, element, role);
  const note = verdict.ok ? actionNote(verdict, role) : null;
  return (
    <section className="pv-section" aria-labelledby={`${prefix}-h`}>
      <h3 id={`${prefix}-h`}>{ROLE_NAMES[role0] ?? role0} <span className="mono muted small">{role0}</span></h3>
      <p className="small muted">Page {element.page ?? "–"}{element.raw_role ? ` · originally tagged ${element.raw_role}` : ""}
        {map.elements.some((e) => e.orig) ? (element.orig ? " · from the original document" : " · added by this service") : ""}</p>
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
          <ItemStatus f={f} />
        </div>
      ))}
      <fieldset disabled={!canEdit || decorative} className="pv-fields">
        {readings && (isFigure || readable) && (
          <ReadingPicker readings={readings} prefix={prefix} value={isFigure ? alt : actual}
            onPick={(text) => { if (isFigure) setAlt(text); else setActual(text); setPicked(true); }} />
        )}
        <div className="field">
          <label htmlFor={`${prefix}-role`}>Tag type</label>
          <select id={`${prefix}-role`} value={role} onChange={(e) => setRole(e.target.value)}>
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
            <label htmlFor={`${prefix}-scope`}>This header describes</label>
            <select id={`${prefix}-scope`} value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="Column">its column</option>
              <option value="Row">its row</option>
              <option value="Both">its row and column</option>
            </select>
          </div>
        )}
        {choices.length > 0 && (
          <fieldset className="pv-headers">
            <legend>Read with these headers</legend>
            <span className="hint">A screen reader can say these before the cell. With none ticked, it goes by each
              header&apos;s scope.</span>
            {choices.map((h) => (
              <label key={h.i} className="check">
                <input type="checkbox" checked={named.includes(h.i)}
                  onChange={(e) => setNamed(e.target.checked ? [...named, h.i] : named.filter((x) => x !== h.i))} />
                <span>{h.text ? `“${h.text.slice(0, 60)}”` : <span className="muted">(empty header)</span>}
                  {/* the headers it's under tell two "Q1"s apart */}
                  {h.headers?.length ? <span className="muted small"> · under {h.headers.map((u) => `“${(map.elements[u]?.text ?? "").slice(0, 30)}”`).join(", ")}</span> : null}
                  {h.page !== element.page && h.page ? <span className="muted small"> · page {h.page}</span> : null}</span>
              </label>
            ))}
          </fieldset>
        )}
        {isLink && (
          <div className="field">
            <label htmlFor={`${prefix}-ltext`}>Link description <span className="hint">(what a screen reader says for
              this link, e.g. "Annual report, PDF")</span></label>
            <input id={`${prefix}-ltext`} type="text" value={linkText} maxLength={4000}
              onChange={(e) => setLinkText(e.target.value)} />
            <span className="hint">Say where it goes, not "click here". It can be changed but not left empty.</span>
          </div>
        )}
        {isForm && (
          <div className="field">
            <label htmlFor={`${prefix}-flabel`}>Field label <span className="hint">(what a screen reader announces for
              this field, e.g. "Last name")</span></label>
            <input id={`${prefix}-flabel`} type="text" value={fieldLabel} maxLength={300}
              onChange={(e) => setFieldLabel(e.target.value)} />
          </div>
        )}
        {isFigure && (
          <div className="field">
            <label htmlFor={`${prefix}-alt`}>Alt text</label>
            <textarea id={`${prefix}-alt`} value={alt} maxLength={4000} onChange={(e) => setAlt(e.target.value)} />
            {suggested && suggested !== alt && (
              <button type="button" className="linkbtn small" onClick={() => setAlt(suggested)}>Use the suggested alt text</button>
            )}
          </div>
        )}
        {readable && (
          <div className="field">
            <label htmlFor={`${prefix}-actual`}>Read aloud as <span className="hint">(optional; leave empty to read the drawn text)</span></label>
            <textarea id={`${prefix}-actual`} value={actual} maxLength={4000} onChange={(e) => setActual(e.target.value)} />
            <span className="hint">Use this when the drawn text reads wrongly: math and symbols, or letters drawn out of order.</span>
            {!actual && element.text && (
              <button type="button" className="linkbtn small" onClick={() => setActual(element.text ?? "")}>Start from the drawn text</button>
            )}
          </div>
        )}
        <div className="field">
          <label htmlFor={`${prefix}-lang`}>Language <span className="hint">(only if different from the document, e.g. fr-CA)</span></label>
          <input id={`${prefix}-lang`} type="text" value={lang} aria-invalid={!langOk} onChange={(e) => setLang(e.target.value)} />
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
          <button className="btn secondary small" data-act="delete-tag" disabled={element.parent === null} onClick={() => setDeleting(true)}
            title={element.parent === null ? "The document's top tag can't be deleted" : undefined}>Delete this tag…</button>
        </div>
      )}
      {deleting && <DeleteTag map={map} element={element} ops={ops} onCancel={() => setDeleting(false)}
        onDelete={(children) => {
          setDeleting(false);
          setOps(addEdit(ops, { op: "delete", element: element.i, expect_role: element.role, children }));
          const up = draftTree(map, ops).parent.get(element.i);
          if (up !== null && up !== undefined) onSelect(up);
        }} />}
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

/** Split anywhere in a piece of text, folded away in the panel. */
function SplitInText({ element, onSplit }: { element: TagElement; onSplit: (at: number, char: number) => void }) {
  if (!splitPieces(element).length) return null;
  return (
    <details className="pv-split-text">
      <summary className="small">Split somewhere else in the text</summary>
      <SplitPicker element={element} onSplit={onSplit} prefix="split" />
    </details>
  );
}

/** Split anywhere in a piece of text: put the cursor where the new tag should start. In the panel and in the
 * "Split" dialog (`prefix` keeps their ids apart). */
export function SplitPicker({ element, onSplit, prefix }: { element: TagElement; onSplit: (at: number, char: number) => void; prefix: string }) {
  const pieces = splitPieces(element);
  const [k, setK] = useState(pieces[0]?.k ?? 0);
  const [pos, setPos] = useState<number | null>(null);
  if (!pieces.length) return null;
  const piece = pieces.find((p) => p.k === k) ?? pieces[0];
  const ok = pos !== null && pos > 0 && pos < piece.text.length && piece.text.slice(0, pos).trim() !== "" && piece.text.slice(pos).trim() !== "";
  const track = (e: { currentTarget: HTMLTextAreaElement }) => setPos(e.currentTarget.selectionStart);
  return (
    <>
      {pieces.length > 1 && (
        <div className="field">
          <label htmlFor={`${prefix}-piece`} className="small">Piece of text</label>
          <select id={`${prefix}-piece`} value={piece.k} onChange={(e) => { setK(Number(e.target.value)); setPos(null); }}>
            {pieces.map((p, n) => <option key={p.k} value={p.k}>{n + 1}: {p.text.slice(0, 40)}</option>)}
          </select>
        </div>
      )}
      <div className="field">
        <label htmlFor={`${prefix}-text`} className="small">Click where the new tag should start</label>
        <textarea id={`${prefix}-text`} readOnly value={piece.text} rows={Math.min(6, 2 + Math.floor(piece.text.length / 40))}
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
    </>
  );
}

const KIND_NAMES: Record<string, string> = { text: "Text", image: "Image", drawing: "Drawing", path: "Drawing" };
const ARTIFACT_NAMES: Record<string, string> = { Pagination: "page header or footer", Layout: "layout", Page: "page", Background: "background" };

/** Where a new tag made from an area goes: next to the tag read just before it (or before the first one), moved up
 * the tree until the new type is allowed there (a paragraph can't go inside a table row). */
function anchorFor(map: TagMap, ops: TagEdit[], area: Area, role: string): { el: TagElement; position: "before" | "after" } | null {
  const list = pageOrder(map, area.page, ops);
  let pick: { el: TagElement; position: "before" | "after" } | null = null;
  if (list.length) {
    const above = list.filter((e) => e.boxes.some((b) => b.page === area.page && b.bbox[1] < area.bbox[1] - 1));
    pick = above.length ? { el: above[above.length - 1], position: "after" } : { el: list[0], position: "before" };
  } else {
    const own = map.elements.filter((e) => e.own && e.boxes.length).sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    const before = own.filter((e) => e.boxes[0].page < area.page);
    const after = own.find((e) => e.boxes[0].page > area.page);
    pick = before.length ? { el: before[before.length - 1], position: "after" } : after ? { el: after, position: "before" } : null;
  }
  if (!pick) return null;
  const tree = draftTree(map, ops);
  const over = overrides(ops, map);
  const roleOf = (i: number | null | undefined) => (i == null ? null : over.get(i)?.role ?? map.elements[i]?.role ?? null);
  let el: TagElement | undefined = pick.el;
  while (el) {
    const parent: number | null = tree.parent.get(el.i) ?? null;
    if (parent === null) return null;
    if (checkMove(role, "Document", roleOf(parent), false).ok) return { el, position: pick.position };
    el = map.elements[parent];
  }
  return null;
}

/** Where an area's new tags go: next to the tag read before the area, or, when every tag near it has been deleted
 * (a page whose tags are all being redone), at the end of the document's top tag. */
function regroupAnchor(map: TagMap, ops: TagEdit[], area: Area, role: string): { el: TagElement; position: "before" | "after" | "in" } | null {
  const near = anchorFor(map, ops, area, role);
  if (near) return near;
  const tree = draftTree(map, ops);
  const top = (tree.kids.get(-1) ?? []).find((i) => !tree.deleted.has(i));
  return top !== undefined && map.elements[top] ? { el: map.elements[top], position: "in" } : null;
}

/** Whether a picture the page draws lies under most of the area (a poster's artwork, a scan): then a part of it can
 * be made a figure of its own. */
function overPicture(map: TagMap, area: Area): boolean {
  const [x0, y0, x1, y1] = area.bbox;
  const size = (x1 - x0) * (y1 - y0);
  const under = (b: [number, number, number, number]) =>
    Math.max(0, Math.min(x1, b[2]) - Math.max(x0, b[0])) * Math.max(0, Math.min(y1, b[3]) - Math.max(y0, b[1]));
  return size > 0 && [...(map.artifacts ?? []).filter((a) => a.kinds.includes("image")), ...(map.pieces ?? []).filter((p) => p.kind === "image")]
    .some((x) => x.page === area.page && under(x.bbox) >= 0.8 * size);
}

/** The drawn area, over a picture, made a figure of its own: that part of the picture is drawn again as the figure's
 * content (the page looks the same), read with the alt text given here. */
function PicturePart({ map, ops, setOps, area, describe, prefix, onAdded }: {
  map: TagMap; ops: TagEdit[]; setOps: (o: TagEdit[]) => void; area: Area; describe?: Describe; prefix: string;
  onAdded: (note: string) => void;
}) {
  const [alt, setAlt] = useState("");
  const [asking, setAsking] = useState("");
  const anchor = regroupAnchor(map, ops, area, "Figure");
  const ask = async () => {
    if (!describe) return;
    setAsking("busy");
    try {
      setAlt(await describe(area.page, area.bbox));
      setAsking("");
    } catch (e) {
      setAsking(e instanceof Error ? e.message : "The AI couldn't describe it just now.");
    }
  };
  const make = () => {
    if (!anchor || !alt.trim()) return;
    setOps([...ops, { op: "regroup", element: anchor.el.i, expect_role: anchor.el.role, position: anchor.position, page: area.page,
      groups: [{ role: "Figure", box: area.bbox, alt: alt.trim() }] }]);
    setAlt("");
    onAdded("A figure of this part of the picture added to the unsaved changes.");
  };
  return (
    <>
      <h4 className="small">A part of a picture</h4>
      <p className="muted small">The area is over a picture. It can be a figure of its own, read with its alt text; the
        page doesn't change.</p>
      <div className="field">
        <label htmlFor={`${prefix}-part-alt`} className="small">Alt text for this part</label>
        <textarea id={`${prefix}-part-alt`} rows={2} value={alt} maxLength={4000} onChange={(e) => setAlt(e.target.value)} />
        {describe && (
          <span className="pv-fig-ask">
            <button type="button" className="btn secondary small" disabled={asking === "busy"} onClick={() => void ask()}>
              {asking === "busy" ? "Describing…" : "Describe with AI"}
            </button>
            <span className="hint">Shows the AI the area you drew. One AI call at this document's AI level. Check what it writes.</span>
          </span>
        )}
        {asking && asking !== "busy" && <span className="small" role="alert">{asking}</span>}
      </div>
      <div className="actions">
        <button className="btn small" disabled={!anchor || !alt.trim()} onClick={make}>Make it a figure</button>
      </div>
      {!anchor && <p className="muted small">There's no tag on or near this page to place the figure next to.</p>}
    </>
  );
}

/** "Suggest tags with AI" for an area: its content (tagged pieces and untagged items) and a picture of it go to the AI,
 * which groups them into tags in reading order. The person checks each tag (its type, its alt text, whether to use
 * it) before the tags join the unsaved changes as one edit; what the AI leaves out stays as it is. */
function AreaSuggestion({ map, ops, setOps, area, pieces, arts, suggest, prefix, onAdded, onBoxes }: {
  map: TagMap; ops: TagEdit[]; setOps: (o: TagEdit[]) => void; area: Area;
  pieces: NonNullable<TagMap["pieces"]>; arts: NonNullable<TagMap["artifacts"]>; suggest: Suggest; prefix: string;
  onAdded: (note: string) => void; onBoxes?: (b: AreaBoxes | null) => void;
}) {
  type Item = { kind: string; text: string; bbox: [number, number, number, number]; piece?: number; section?: number };
  // box: a part of a picture the page draws (a poster's artwork), as the AI placed it on the page
  type Row = { role: string; items: number[]; alt: string; use: boolean; box?: [number, number, number, number] };
  const items: Item[] = [
    ...pieces.map((p) => ({ kind: p.kind, text: p.text ?? "", bbox: p.bbox, piece: p.mcid })),
    ...arts.map((a) => ({ kind: a.text ? "text" : a.kinds[0] ?? "drawing", text: a.text ?? "", bbox: a.bbox, section: a.seq })),
  ];
  const [rows, setRows] = useState<Row[] | null>(null);
  const [left, setLeft] = useState({ decorative: 0, unplaced: 0 });
  const [state, setState] = useState(""); // "busy" while the AI works, else what went wrong
  const ask = async () => {
    setState("busy");
    try {
      const got = await suggest(area.page, area.bbox, items.map(({ kind, text, bbox }) => ({ kind, text: text.slice(0, 300), bbox })));
      const placed = new Set(got.tags.flatMap((t) => t.items));
      setRows(got.tags.map((t) => ({ role: NEW_AREA_ROLES.includes(t.role) ? t.role : "P", items: t.items, alt: t.alt, use: true,
        ...(t.box ? { box: t.box } : {}) })));
      setLeft({ decorative: got.decorative.length, unplaced: items.length - placed.size - got.decorative.length });
      setState("");
    } catch (e) {
      setState(e instanceof Error ? e.message : "The AI couldn't suggest tags just now.");
    }
  };
  // The AI's figures drawn on the page while they're being checked, numbered as in the list
  useEffect(() => {
    onBoxes?.(rows ? { page: area.page, boxes: rows.flatMap((r, n) => (r.box && r.use ? [{ bbox: r.box, label: String(n + 1) }] : [])) } : null);
  }, [rows, area.page, onBoxes]);
  useEffect(() => () => onBoxes?.(null), [onBoxes]);
  const used = (rows ?? []).filter((r) => r.use);
  const missingAlt = used.some((r) => (r.role === "Figure" || r.role === "Formula") && !r.alt.trim());
  const anchor = regroupAnchor(map, ops, area, "P");
  const say = (r: Row) => {
    const texts = r.items.map((i) => items[i]?.text.trim()).filter(Boolean).join(" ");
    const others = r.items.filter((i) => !items[i]?.text.trim()).length;
    return [r.box && "The part of the picture outlined on the page", texts && `“${texts.slice(0, 80)}${texts.length > 80 ? "…" : ""}”`,
      others && `${others} picture${others > 1 ? "s or drawings" : " or drawing"}`].filter(Boolean).join(" and ");
  };
  const set = (n: number, change: Partial<Row>) => setRows((rows ?? []).map((r, k) => (k === n ? { ...r, ...change } : r)));
  function add() {
    if (!anchor || !used.length || missingAlt) return;
    const groups: AreaGroup[] = used.map((r) => {
      const ps = r.items.map((i) => items[i]?.piece).filter((m): m is number => m !== undefined);
      const ss = r.items.map((i) => items[i]?.section).filter((s): s is number => s !== undefined);
      return { role: r.role, ...(ps.length ? { pieces: ps } : {}), ...(ss.length ? { sections: ss } : {}),
        ...(r.box ? { box: r.box } : {}), ...(r.role === "Figure" || r.role === "Formula" ? { alt: r.alt.trim() } : {}) };
    });
    setOps([...ops, { op: "regroup", element: anchor.el.i, expect_role: anchor.el.role, position: anchor.position,
      page: area.page, groups }]);
    setRows(null);
    onAdded(`${groups.length} new tag${groups.length > 1 ? "s" : ""} added to the unsaved changes.`);
  }
  return (
    <div className="pv-area-ai">
      {!rows ? (
        <span className="pv-fig-ask">
          <button type="button" className="btn secondary small" disabled={state === "busy"} onClick={() => void ask()}>
            {state === "busy" ? "Asking the AI…" : "Suggest tags with AI"}
          </button>
          <span className="hint">Shows the AI the area{items.length ? ` and the ${items.length} item${items.length > 1 ? "s" : ""} of content in it` : ""}:
            it suggests headings, paragraphs and figures, parts of a picture included. One AI call at this document's AI
            level. You check what it suggests.</span>
        </span>
      ) : (
        <>
          <h4 className="small">The AI's tags for the area, in reading order</h4>
          <ol className="pv-area-ai-list">
            {rows.map((r, n) => (
              <li key={n}>
                <label className="check small">
                  <input type="checkbox" checked={r.use} onChange={(e) => set(n, { use: e.target.checked })} />
                  <span>{say(r) || "Nothing readable"}</span>
                </label>
                <div className="pv-area-ai-row">
                  <label className="small" htmlFor={`${prefix}-ai-${n}`}>Tag as</label>
                  <select id={`${prefix}-ai-${n}`} value={r.role} disabled={!r.use} onChange={(e) => set(n, { role: e.target.value })}>
                    {(r.box ? ["Figure", "Formula"] : NEW_AREA_ROLES).map((x) => <option key={x} value={x}>{ROLE_NAMES[x] ? `${ROLE_NAMES[x]} (${x})` : x}</option>)}
                  </select>
                </div>
                {r.use && (r.role === "Figure" || r.role === "Formula") && (
                  <div className="field">
                    <label className="small" htmlFor={`${prefix}-ai-alt-${n}`}>Alt text</label>
                    <textarea id={`${prefix}-ai-alt-${n}`} rows={2} value={r.alt} maxLength={4000} onChange={(e) => set(n, { alt: e.target.value })} />
                  </div>
                )}
              </li>
            ))}
          </ol>
          {(left.decorative > 0 || left.unplaced > 0) && (
            <p className="muted small">
              {left.decorative > 0 && `The AI took ${left.decorative} item${left.decorative > 1 ? "s" : ""} for decoration. `}
              {left.unplaced > 0 && `It didn't place ${left.unplaced} item${left.unplaced > 1 ? "s" : ""}. `}
              Those stay as they are.
            </p>
          )}
          <div className="actions">
            <button className="btn small" disabled={!anchor || !used.length || missingAlt} onClick={add}>Add these tags</button>
            <button className="btn secondary small" onClick={() => setRows(null)}>Discard</button>
          </div>
          <p className="muted small">
            {!used.length ? "Check the tags to add." : missingAlt ? "A figure or formula needs alt text."
              : !anchor ? "There's no tag on or near this page to place the new tags next to."
              : "They go where the first of their content was read. Content they take leaves its tags; a tag left with nothing is removed."}
          </p>
        </>
      )}
      {state && state !== "busy" && <p className="small" role="alert">{state}</p>}
    </div>
  );
}

/** What's in an area of a page, to move into the selected tag or to tag. In the panel, and in the "Area" dialog that
 * opens when an area is chosen (`prefix` keeps their ids apart). */
export function AreaEdit({ map, page, element, ops, setOps, area, setArea, areaMode, setAreaMode, onSelect, prefix = "area", onFigure, describe, suggest, onBoxes }: {
  map: TagMap; page: number; element: TagElement | null; ops: TagEdit[]; setOps: (o: TagEdit[]) => void;
  area: Area | null; setArea: (a: Area | null) => void; areaMode: boolean; setAreaMode: (on: boolean) => void;
  onSelect: (i: number) => void; prefix?: string;
  // Part of a figure: open the figure split with these pieces chosen (`keys` are "page:mcid")
  onFigure?: (el: number, keys: string[]) => void;
  describe?: Describe;
  suggest?: Suggest;
  onBoxes?: (b: AreaBoxes | null) => void; // the AI's figures for the area, to draw on the page
}) {
  const [skipPieces, setSkipPieces] = useState<Set<string>>(new Set());
  const [skipArts, setSkipArts] = useState<Set<number>>(new Set());
  const [role, setRole] = useState("P");
  const [alt, setAlt] = useState("");
  const [note, setNote] = useState("");
  const [asking, setAsking] = useState(""); // "busy" while the AI describes the area, else what went wrong
  const [tab, setTab] = useState<AreaTab | null>(null); // null: the area's first tab (areaTab)
  useEffect(() => { setSkipPieces(new Set()); setSkipArts(new Set()); setNote(""); setAsking(""); setTab(null); }, [area]);
  useEffect(() => {
    if (!areaMode) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setAreaMode(false); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [areaMode, setAreaMode]);

  if (!map.pieces || !map.artifacts) {
    return (
      <section className="pv-section" aria-labelledby={`${prefix}-h`}>
        <h3 id={`${prefix}-h`}>Areas</h3>
        <p className="muted small">Changing the area a tag covers isn't available for this version: it was made before
          that was added. Saving any change makes a version where it is.</p>
      </section>
    );
  }
  const pending = pendingArea(ops);
  const over = overrides(ops, map);
  const gone = absorbedBy(ops);
  const holder = (i: number) => gone.get(i) ?? i;
  // Content of a tag a pending change deletes (or marks as an artifact): untagged once saved, so it can be moved into a
  // tag or made a new tag now (#692), but not split out of its tag (that tag is gone by then)
  const deleted = deletedTags(map, ops);
  const doomed = (p: { el: number }) => deleted.has(holder(p.el)) || !!over.get(holder(p.el))?.decorative;
  const pieces = area ? map.pieces.filter((p) => p.page === area.page && inside(area.bbox, p.bbox) >= 0.6) : [];
  const arts = area ? map.artifacts.filter((a) => a.page === area.page && inside(area.bbox, a.bbox) >= 0.6) : [];
  const usableArt = (a: (typeof arts)[number]) => !a.nested && inside(area!.bbox, a.bbox) >= 0.98 && !pending.sections.has(`${a.page}:${a.seq}`);
  const chosenAll = pieces.filter((p) => !skipPieces.has(`${p.page}:${p.mcid}`) && !pending.pieces.has(`${p.page}:${p.mcid}`));
  const chosenPieces = chosenAll.filter((p) => !doomed(p));
  const chosenFreed = chosenAll.filter(doomed);  // a deleted tag's content: untagged, to tag again
  const chosenArts = arts.filter((a) => usableArt(a) && !skipArts.has(a.seq));
  const target = element ? { ...element, role: over.get(element.i)?.role ?? element.role } : null;
  const canHold = !!target && CONTENT_ROLES.has(target.role) && !deleted.has(target.i);
  const toMove = target ? chosenAll.filter((p) => holder(p.el) !== target.i) : [];
  const anchor = area && chosenArts.length + chosenFreed.length
    ? (chosenFreed.length ? regroupAnchor(map, ops, area, role) : anchorFor(map, ops, area, role)) : null;
  // The checked pieces are part of one figure, not all of it: they can become a figure of their own
  const holders = [...new Set(chosenPieces.map((p) => holder(p.el)))];
  const fig = holders.length === 1 ? map.elements[holders[0]] : undefined;
  const figPart = !!fig && ["Figure", "Formula"].includes(over.get(fig.i)?.role ?? fig.role)
    && map.pieces.filter((p) => holder(p.el) === fig.i).length > chosenPieces.length;
  const needsAlt = role === "Figure" || role === "Formula";
  const short = (t?: string) => (t ? `“${t.slice(0, 50)}${t.length > 50 ? "…" : ""}”` : "");
  const tagLabel = (e: TagElement) => `${over.get(e.i)?.role ?? e.role}${e.text ? ` ${short(e.text)}` : ""}`;
  // A deleted tag's content in the area, shown with the untagged content too
  const freed = pieces.filter((p) => !pending.pieces.has(`${p.page}:${p.mcid}`) && doomed(p));
  const shown = tab === "ai" && !suggest ? "tagged" : tab ?? areaTab(pieces.length, arts.length + (area && overPicture(map, area) ? 1 : 0));

  function assign() {
    if (!target || !toMove.length) return;
    setOps([...ops, { op: "assign", element: target.i, expect_role: element!.role, pieces: toMove.map((p) => ({ page: p.page, mcid: p.mcid })) }]);
    setNote(`${toMove.length} piece${toMove.length > 1 ? "s" : ""} of content will move into ${tagLabel(target)}.`);
  }
  async function ask() {
    if (!describe || !area) return;
    setAsking("busy");
    try {
      setAlt(await describe(area.page, area.bbox));
      setAsking("");
    } catch (e) {
      setAsking(e instanceof Error ? e.message : "The AI couldn't describe it just now.");
    }
  }
  function tagArtifacts() {
    const count = chosenArts.length + chosenFreed.length;
    if (!area || !anchor || !count || (needsAlt && !alt.trim())) return;
    const a = needsAlt ? { alt: alt.trim() } : {};
    // With a deleted tag's content among them, the new tag takes it back from its artifact sections (regroup)
    setOps([...ops, chosenFreed.length
      ? { op: "regroup", element: anchor.el.i, expect_role: anchor.el.role, position: anchor.position, page: area.page,
        groups: [{ role, sections: chosenArts.map((x) => x.seq), pieces: chosenFreed.map((p) => p.mcid), ...a }] }
      : { op: "tag_artifacts", element: anchor.el.i, expect_role: anchor.el.role, position: anchor.position as "before" | "after",
        page: area.page, sections: chosenArts.map((x) => x.seq), role, ...a }]);
    setNote(`A new ${ROLE_NAMES[role] ?? role} will be made from ${count} untagged item${count > 1 ? "s" : ""}.`);
    setAlt("");
  }

  return (
    <section className="pv-section" aria-labelledby={`${prefix}-h`}>
      <h3 id={`${prefix}-h`}>Areas</h3>
      <p className="muted small">Choose an area of the page to see what's in it: move tagged content into the selected
        tag, or tag content that isn't read at all (an artifact).</p>
      <div className="actions">
        <button className="btn secondary small" aria-pressed={areaMode} onClick={() => setAreaMode(!areaMode)}>
          {areaMode ? "Stop choosing an area" : "Draw an area on the page"}
        </button>
        <button className="btn secondary small" onClick={() => {
          const p = map.pages[page - 1];
          if (p) { setArea({ page, bbox: [0, 0, p.w, p.h] }); setAreaMode(false); }
        }}>Use all of page {page}</button>
        {area && <button className="linkbtn small" onClick={() => setArea(null)}>Clear the area</button>}
      </div>
      {areaMode && <p className="small" role="status">Drag on a page on the right to draw the area. Escape stops.</p>}
      {area && (
        <>
          <TabList label="What to do with the area" small idPrefix={prefix} active={shown} onPick={(k) => setTab(k as AreaTab)}
            items={[{ key: "tagged", label: "Tagged content", count: pieces.length }, { key: "new", label: "New tag" },
              ...(suggest ? [{ key: "ai", label: "Suggest tags with AI" }] : [])]} />
          {/* Every panel stays mounted, hidden when its tab isn't chosen: what's checked or typed, or what the AI
              suggested, is still there when its tab is chosen again */}
          <div role="tabpanel" id={`${prefix}-panel-tagged`} aria-labelledby={`${prefix}-tab-tagged`} hidden={shown !== "tagged"}
            className="pv-area-panel">
            <h4 className="small">Tagged content in the area on page {area.page} ({pieces.length})</h4>
            {pieces.length === 0 ? <p className="muted small">None.</p> : (
              <ul className="pv-area-list">
                {pieces.map((p) => {
                  const key = `${p.page}:${p.mcid}`;
                  const movedTo = pending.pieces.get(key);
                  const owner = map.elements[holder(movedTo ?? p.el)];
                  const dead = movedTo === undefined && doomed(p);
                  return (
                    <li key={key}>
                      <label className="check small">
                        <input type="checkbox" disabled={movedTo !== undefined} checked={movedTo === undefined && !skipPieces.has(key)}
                          onChange={(e) => { const n = new Set(skipPieces); if (e.target.checked) n.delete(key); else n.add(key); setSkipPieces(n); }} />
                        <span>
                          {KIND_NAMES[p.kind] ?? p.kind} {short(p.text)}{" "}
                          {dead ? <span className="muted">in {owner ? tagLabel(owner) : "a tag"}, which a change you've added
                            deletes: untagged once saved, so it can go into the selected tag, or a new tag (the New tag tab)</span> : (
                            movedTo === -1 ? <span className="muted">going into a new tag</span> : (
                              <span className="muted">{movedTo !== undefined ? "moving to " : "in "}
                                {owner ? <button className="linkbtn" onClick={() => onSelect(owner.i)}>{tagLabel(owner)}</button> : "a tag"}
                              </span>
                            )
                          )}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
            {pieces.length > 0 && (
              <>
                <div className="actions">
                  <button className="btn small" disabled={!canHold || !toMove.length} onClick={assign}>
                    Move checked content into the selected tag
                  </button>
                  {onFigure && figPart && fig && (
                    <button className="btn secondary small" onClick={() => onFigure(fig.i, chosenPieces.map((p) => `${p.page}:${p.mcid}`))}>
                      Make them a {(over.get(fig.i)?.role ?? fig.role) === "Formula" ? "formula" : "figure"} of their own…
                    </button>
                  )}
                </div>
                <p className="muted small">
                  {!target ? "Select the tag that should hold this content first (on the page or in the tag tree)."
                    : deleted.has(target.i) ? "The selected tag is being deleted. Select the tag that should hold this content."
                    : !canHold ? `A ${ROLE_NAMES[target.role] ?? target.role} only holds other tags. Select a paragraph, heading, figure, cell or list item body.`
                    : !chosenAll.length ? "Nothing here can be moved now: check some content first."
                    : !toMove.length ? `The checked content is already in ${tagLabel(target)}.`
                    : `Moves ${toMove.length} piece${toMove.length > 1 ? "s" : ""} into ${tagLabel(target)}. A tag left with nothing is removed.`}
                </p>
              </>
            )}
          </div>
          <div role="tabpanel" id={`${prefix}-panel-new`} aria-labelledby={`${prefix}-tab-new`} hidden={shown !== "new"}
            className="pv-area-panel">
            <h4 className="small">Untagged content (artifacts) in the area ({arts.length + freed.length})</h4>
            {arts.length + freed.length === 0 ? <p className="muted small">None.</p> : (
              <ul className="pv-area-list">
                {freed.map((p) => {
                  const key = `${p.page}:${p.mcid}`;
                  return (
                    <li key={key}>
                      <label className="check small">
                        <input type="checkbox" checked={!skipPieces.has(key)}
                          onChange={(e) => { const n = new Set(skipPieces); if (e.target.checked) n.delete(key); else n.add(key); setSkipPieces(n); }} />
                        <span>{KIND_NAMES[p.kind] ?? p.kind} {short(p.text)} <span className="muted">(its tag is being deleted)</span></span>
                      </label>
                    </li>
                  );
                })}
                {arts.map((a) => {
                  const why = pending.sections.has(`${a.page}:${a.seq}`) ? "being tagged by a change you've added"
                    : a.nested ? "inside another untagged section; it can't be tagged on its own"
                    : inside(area.bbox, a.bbox) < 0.98 ? "goes outside the area; draw a larger one to include all of it" : "";
                  return (
                    <li key={a.seq}>
                      <label className="check small">
                        <input type="checkbox" disabled={!!why} checked={!why && !skipArts.has(a.seq)}
                          onChange={(e) => { const n = new Set(skipArts); if (e.target.checked) n.delete(a.seq); else n.add(a.seq); setSkipArts(n); }} />
                        <span>
                          {a.text ? `Text ${short(a.text)}` : a.kinds.map((k) => KIND_NAMES[k] ?? k).filter((v, n, all) => all.indexOf(v) === n).join(", ") || "Content"}
                          {a.type && <span className="muted"> ({ARTIFACT_NAMES[a.type] ?? a.type.toLowerCase()})</span>}
                          {why && <span className="muted">: {why}</span>}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
            {arts.length + freed.length > 0 && (
              <>
                <div className="field">
                  <label htmlFor={`${prefix}-role`} className="small">Tag the checked items as</label>
                  <select id={`${prefix}-role`} value={role} onChange={(e) => setRole(e.target.value)}>
                    {NEW_AREA_ROLES.map((r) => <option key={r} value={r}>{ROLE_NAMES[r] ? `${ROLE_NAMES[r]} (${r})` : r}</option>)}
                  </select>
                </div>
                {needsAlt && (
                  <div className="field">
                    <label htmlFor={`${prefix}-alt`} className="small">Alt text</label>
                    <textarea id={`${prefix}-alt`} value={alt} maxLength={4000} onChange={(e) => setAlt(e.target.value)} />
                    {describe && (
                      <span className="pv-fig-ask">
                        <button type="button" className="btn secondary small" disabled={asking === "busy"} onClick={() => void ask()}>
                          {asking === "busy" ? "Describing…" : "Describe with AI"}
                        </button>
                        <span className="hint">Shows the AI the area you drew. One AI call at this document's AI level. Check what it writes.</span>
                      </span>
                    )}
                    {asking && asking !== "busy" && <span className="small" role="alert">{asking}</span>}
                  </div>
                )}
                <div className="actions">
                  <button className="btn small" disabled={!(chosenArts.length + chosenFreed.length) || !anchor || (needsAlt && !alt.trim())} onClick={tagArtifacts}>
                    Tag checked items
                  </button>
                </div>
                <p className="muted small">
                  {!chosenArts.length && !chosenFreed.length ? "Check the items to tag."
                    : !anchor ? "There's no tag on or near this page to place the new tag next to."
                    : `One new tag, read ${anchor.position} ${tagLabel(anchor.el)}. Move it in the tag tree afterwards if needed.`}
                </p>
              </>
            )}
            {overPicture(map, area) && (
              <PicturePart key={`${area.page}:${area.bbox.join(",")}`} map={map} ops={ops} setOps={setOps} area={area}
                describe={describe} prefix={prefix} onAdded={setNote} />
            )}
          </div>
          {suggest && (
            <div role="tabpanel" id={`${prefix}-panel-ai`} aria-labelledby={`${prefix}-tab-ai`} hidden={shown !== "ai"}
              className="pv-area-panel">
              <AreaSuggestion key={`${area.page}:${area.bbox.join(",")}`} map={map} ops={ops} setOps={setOps} area={area}
                pieces={pieces.filter((p) => !pending.pieces.has(`${p.page}:${p.mcid}`))}
                arts={arts.filter(usableArt)} suggest={suggest} prefix={prefix} onAdded={setNote} onBoxes={onBoxes} />
            </div>
          )}
        </>
      )}
      <p className="small" role="status" aria-live="polite">{note}</p>
    </section>
  );
}

/** Asks what deleting a tag should do with what's inside it, and says what will stop being read. Only choices that
 * leave a valid tree are offered (the engine checks again when the change is saved). */
export function DeleteTag({ map, element, ops, onCancel, onDelete }: {
  map: TagMap; element: TagElement; ops: TagEdit[]; onCancel: () => void; onDelete: (children: "lift" | "delete") => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => { if (ref.current && !ref.current.open) ref.current.showModal(); }, []);
  const { role, parentRole, kids, going, kept, keepsOwn, single, liftWhy, deleteWhy } = deleteCheck(map, ops, element.i);
  // What it holds directly (not through a tag inside it)
  const ownText = element.own ? (kids.length ? element.say ?? "" : element.say ?? element.actual_text ?? element.text ?? "") : "";
  const allText = (element.alt ?? element.actual_text ?? element.text ?? "").trim();
  const [choice, setChoice] = useState<"lift" | "delete">(!liftWhy && !single ? "lift" : "delete");
  const why = choice === "lift" ? liftWhy : deleteWhy;
  const name = ROLE_NAMES[role] ?? role;
  const parentName = (parentRole && (ROLE_NAMES[parentRole] ?? parentRole)) || "parent";
  const quote = (t: string) => `“${t.slice(0, 160)}${t.length > 160 ? "…" : ""}”`;
  const n = (count: number) => `${count.toLocaleString()} tag${count === 1 ? "" : "s"}`;

  return (
    <dialog ref={ref} className="confirm-dialog" aria-labelledby={`${id}-h`} onCancel={(e) => { e.preventDefault(); onCancel(); }}>
      <h2 id={`${id}-h`}>Delete this {name} tag?</h2>
      {single ? (
        <p>{allText ? <>Its content will no longer be read by screen readers: {quote(allText)}</>
          : "It holds no text. Any picture or drawing in it will no longer be announced."}</p>
      ) : (
        <fieldset className="choices">
          <legend>What should happen to what's inside it?</legend>
          <label className="check">
            <input type="radio" name={`${id}-c`} checked={choice === "lift"} disabled={!!liftWhy} onChange={() => setChoice("lift")} />
            <span><strong>Remove only this tag.</strong>{" "}
              {kids.length > 0 && <>The {n(kids.length)} directly inside it move{kids.length === 1 ? "s" : ""} up a level, into the {parentName}. </>}
              {ownText.trim() && (keepsOwn ? <>Its own text stays, read as part of the {parentName}.</>
                : <>Text it holds directly will no longer be read: {quote(ownText.trim())}</>)}
              {liftWhy && <span className="muted"> Not possible here: {liftWhy}</span>}</span>
          </label>
          <label className="check">
            <input type="radio" name={`${id}-c`} checked={choice === "delete"} disabled={!!deleteWhy} onChange={() => setChoice("delete")} />
            <span><strong>Delete it and everything in it.</strong>{" "}
              {going.length > 0 && <>The {n(going.length)} inside it {going.length === 1 ? "is" : "are"} deleted too. </>}
              {kept.length > 0 && !deleteWhy && <>The {kept.length.toLocaleString()} link{kept.length === 1 ? "" : "s"} and form
                field{kept.length === 1 ? "" : "s"} in it {kept.length === 1 ? "is" : "are"} kept and move{kept.length === 1 ? "s" : ""} up
                into the {parentName}: they have to stay tagged. </>}
              {allText ? <>{kept.length > 0 ? "The rest" : "This"} will no longer be read by screen readers: {quote(allText)}</>
                : "Nothing in it is read any more."}
              {deleteWhy && <span className="muted"> Not possible here: {deleteWhy}</span>}</span>
          </label>
        </fieldset>
      )}
      {single && deleteWhy && <p role="alert">{deleteWhy}</p>}
      <p className="muted small">Nothing changes in the file until you save, and you can undo this under Unsaved changes.</p>
      <div className="actions">
        <button className="btn secondary" type="button" autoFocus onClick={onCancel}>Cancel</button>
        <button className="btn danger" type="button" disabled={!!why} onClick={() => onDelete(choice)}>
          {choice === "lift" && !single ? "Remove the tag" : "Delete"}
        </button>
      </div>
    </dialog>
  );
}

/** The same question for several tags chosen together: one choice for all of them. Each is checked with the ones
 * before it done; a tag that can't be removed that way is named with the reason and left as it is. */
export function DeleteTags({ map, ids, ops, onCancel, onDelete }: {
  map: TagMap; ids: number[]; ops: TagEdit[]; onCancel: () => void;
  onDelete: (next: TagEdit[], done: number[], children: DeleteChoice) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => { if (ref.current && !ref.current.open) ref.current.showModal(); }, []);
  // None holds other tags and none sits where its text could stay: removing is deleting, so there's nothing to choose
  const leaves = ids.every((i) => deleteCheck(map, ops, i).single);
  const [picked, setChoice] = useState<DeleteChoice>("lift");
  const choice: DeleteChoice = leaves ? "delete" : picked;
  const result = deleteMany(map, ops, ids, choice);
  const before = draftTree(map, ops).deleted.size;
  const bare = leaves ? 0 : result.done.filter((i) => deleteCheck(map, ops, i).single).length;
  const alsoGoing = draftTree(map, result.ops).deleted.size - before - result.done.length;
  const n = (count: number) => `${count.toLocaleString()} tag${count === 1 ? "" : "s"}`;
  const label = (i: number) => {
    const e = map.elements[i];
    const text = (e?.alt ?? e?.text ?? "").trim();
    return `${ROLE_NAMES[e?.role ?? ""] ?? e?.role}${text ? ` “${text.slice(0, 40)}${text.length > 40 ? "…" : ""}”` : ""}`;
  };

  return (
    <dialog ref={ref} className="confirm-dialog" aria-labelledby={`${id}-h`} onCancel={(e) => { e.preventDefault(); onCancel(); }}>
      <h2 id={`${id}-h`}>Delete these {ids.length.toLocaleString()} tags?</h2>
      {leaves ? <p>Their content will no longer be read by screen readers.</p> : <fieldset className="choices">
        <legend>What should happen to what's inside them?</legend>
        <label className="check">
          <input type="radio" name={`${id}-c`} checked={choice === "lift"} onChange={() => setChoice("lift")} />
          <span><strong>Remove only these tags.</strong> The tags inside each move up a level, into the tag around it.
            Text a tag holds directly stays when the tag around it can take it, or stops being read.</span>
        </label>
        <label className="check">
          <input type="radio" name={`${id}-c`} checked={choice === "delete"} onChange={() => setChoice("delete")} />
          <span><strong>Delete them and everything in them.</strong> What they hold is no longer read by screen readers.
            Links and form fields in them are kept and move up a level: they have to stay tagged.</span>
        </label>
      </fieldset>}
      <p aria-live="polite">
        {result.done.length ? <>{choice === "lift" ? "Removes" : "Deletes"} {n(result.done.length)}</> : "None of them can be removed this way"}
        {choice === "delete" && alsoGoing > 0 && <>, and the {n(alsoGoing)} inside them</>}.
        {choice === "lift" && bare > 0 && <> {bare === 1 ? "One holds" : `${bare.toLocaleString()} hold`} no other tags, so
          {bare === 1 ? " its" : " their"} content is no longer read.</>}
        {result.within.length > 0 && <> {n(result.within.length)} {result.within.length === 1 ? "is" : "are"} inside
          another being deleted, or deleted already, and go{result.within.length === 1 ? "es" : ""} with it.</>}
      </p>
      {result.refused.length > 0 && (
        <div>
          <p>{result.refused.length === 1 ? "One is" : `${result.refused.length.toLocaleString()} are`} left as {result.refused.length === 1 ? "it is" : "they are"}:</p>
          <ul className="small">
            {result.refused.slice(0, 8).map((r) => <li key={r.i}>{label(r.i)}: {r.why}</li>)}
            {result.refused.length > 8 && <li>and {(result.refused.length - 8).toLocaleString()} more</li>}
          </ul>
        </div>
      )}
      <p className="muted small">Nothing changes in the file until you save, and you can undo this under Unsaved changes.</p>
      <div className="actions">
        <button className="btn secondary" type="button" autoFocus onClick={onCancel}>Cancel</button>
        <button className="btn danger" type="button" disabled={!result.done.length} onClick={() => onDelete(result.ops, result.done, choice)}>
          {choice === "lift" ? "Remove" : "Delete"} {n(result.done.length)}
        </button>
      </div>
    </dialog>
  );
}
