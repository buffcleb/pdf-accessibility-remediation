import { useState } from "react";
import type { TagEdit, TagMap } from "../api";
import { addEdit, documentDraft } from "./draft";

const LANG = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$/;
// Suggestions for the language field; any code like these is accepted
const COMMON = [["en-US", "English (US)"], ["en-GB", "English (UK)"], ["es", "Spanish"], ["fr", "French"],
  ["fr-CA", "French (Canada)"], ["de", "German"], ["it", "Italian"], ["pt-BR", "Portuguese (Brazil)"],
  ["zh-Hans", "Chinese (Simplified)"], ["ja", "Japanese"], ["ko", "Korean"], ["ar", "Arabic"], ["he", "Hebrew"],
  ["ru", "Russian"], ["hi", "Hindi"]];

/** The whole document's title (announced when it opens, shown in the title bar) and language (#363). Changes join
 * the unsaved changes, like a tag's. */
export function DocumentPanel({ map, ops, setOps, canEdit, onDone }: {
  map: TagMap; ops: TagEdit[]; setOps: (ops: TagEdit[]) => void; canEdit: boolean; onDone: () => void;
}) {
  const now = documentDraft(ops, map);
  const [title, setTitle] = useState(now.title);
  const [lang, setLang] = useState(now.lang);
  const titleOk = title.trim().length > 0 && title.length <= 500;
  // A tag map from before #363 doesn't say the language: empty is then "unchanged", not wrong
  const langOk = LANG.test(lang.trim()) || (!lang.trim() && !now.lang);
  const changed = title.trim() !== now.title.trim() || lang.trim() !== now.lang;

  const apply = () => {
    let next = ops;
    if (title.trim() !== now.title.trim()) next = addEdit(next, { op: "set_doc_title", title: title.trim() });
    if (lang.trim() !== now.lang) next = addEdit(next, { op: "set_doc_lang", lang: lang.trim() });
    setOps(next);
    onDone();
  };

  return (
    <form className="pv-docpanel" onSubmit={(e) => { e.preventDefault(); if (titleOk && langOk && changed) apply(); }}>
      <h2>Document</h2>
      <fieldset disabled={!canEdit}>
        <legend className="visually-hidden">The document's title and language</legend>
        <div className="field">
          <label htmlFor="doc-title">Title <span className="hint">(what a screen reader announces when the document opens)</span></label>
          <input id="doc-title" type="text" value={title} maxLength={500} aria-invalid={!titleOk}
            aria-describedby={!titleOk ? "doc-title-err" : undefined} onChange={(e) => setTitle(e.target.value)} />
          {!titleOk && <span id="doc-title-err" className="hint bad-text">A document needs a title.</span>}
          {map.document && !map.document.title && <span className="hint">This file has no title yet.</span>}
        </div>
        <div className="field">
          <label htmlFor="doc-lang">Language <span className="hint">(the language most of it is in, e.g. en-US)</span></label>
          <input id="doc-lang" type="text" list="doc-lang-list" value={lang} aria-invalid={!langOk}
            aria-describedby={!langOk ? "doc-lang-err" : undefined} onChange={(e) => setLang(e.target.value)} />
          <datalist id="doc-lang-list">
            {COMMON.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </datalist>
          {!langOk && <span id="doc-lang-err" className="hint bad-text">A language code like en-US or fr.</span>}
          <span className="hint">A passage in another language keeps its own, set on its tag.</span>
        </div>
      </fieldset>
      {canEdit && (
        <div className="actions">
          <button className="btn small" type="submit" disabled={!titleOk || !langOk || !changed}>Add change</button>
        </div>
      )}
    </form>
  );
}
