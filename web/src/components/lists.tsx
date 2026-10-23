import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { fmtNum } from "./ui";

/* One way to page, search and filter lists, everywhere in the app (#514). */

export const PAGE_SIZES = [25, 50, 100, 200];

/** Moving through a list a page at a time: First, Previous, where you are, Next, Last. Put one above the list and one
 * below (`where`). With one page, the one above is just the count ("3 people"); the one below also chooses how many
 * to show. A list with nothing in it says so itself. At the ends the buttons are greyed (aria-disabled, so a keyboard user's focus stays put). Paging from the
 * bottom brings the top of the list (`topId`) back into view. */
export function ListPager({ page, pageSize, total, noun, where, onPage, onSize, topId }: {
  page: number; pageSize: number; total: number;
  noun: [string, string];  // what's listed, one and many: ["document", "documents"]
  where: "top" | "bottom"; onPage: (page: number) => void; onSize?: (size: number) => void; topId?: string;
}) {
  const sizeId = useId();
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (where === "top" && pages < 2) {
    return total ? <p className="small muted list-count" role="status">{fmtNum(total)} {total === 1 ? noun[0] : noun[1]}</p> : null;
  }
  if (where === "bottom" && total <= PAGE_SIZES[0]) return null;
  const first = total ? (page - 1) * pageSize + 1 : 0;
  const last = Math.min(total, page * pageSize);
  const go = (p: number) => {
    if (p < 1 || p > pages || p === page) return;
    onPage(p);
    if (where === "bottom" && topId) document.getElementById(topId)?.scrollIntoView({ block: "start" });
  };
  const button = (label: ReactNode, to: number, off: boolean, name: string) => (
    <button type="button" className="btn secondary small" aria-disabled={off || undefined} aria-label={name}
      onClick={() => { if (!off) go(to); }}>{label}</button>
  );
  return (
    <nav className={`list-pager list-pager-${where}`} aria-label={`Pages of ${noun[1]}${where === "bottom" ? " (below the list)" : ""}`}>
      {button(<><span aria-hidden="true">«</span> First</>, 1, page <= 1, "First page")}
      {button(<><span aria-hidden="true">‹</span> Previous</>, page - 1, page <= 1, "Previous page")}
      <span className="list-pager-where small" role={where === "top" ? "status" : undefined}>Page {fmtNum(page)} of {fmtNum(pages)}
        <span className="muted"> · {fmtNum(first)}–{fmtNum(last)} of {fmtNum(total)} {total === 1 ? noun[0] : noun[1]}</span></span>
      {button(<>Next <span aria-hidden="true">›</span></>, page + 1, page >= pages, "Next page")}
      {button(<>Last <span aria-hidden="true">»</span></>, pages, page >= pages, "Last page")}
      {where === "bottom" && onSize && (
        <label className="small list-pager-size" htmlFor={sizeId}>Show
          <select id={sizeId} value={pageSize} onChange={(e) => onSize(Number(e.target.value))}>
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select> per page</label>
      )}
    </nav>
  );
}

/** A filter in use, shown as a chip that removes it. */
export type Chip = { key: string; text: string };

/** Search and filters above a list: the search box (searching a moment after typing stops), the key filters in the
 * bar (`children`), the rest under "Filters (n)" (`more`), chips for the filters in use, and "Clear search and
 * filters". The list's state lives in the page's address; this only says what changed. */
export function ListBar({ label, searchId, query, onQuery, placeholder, children, more, chips, onRemove, onClear, active = false }: {
  label: string; searchId: string; query: string; onQuery: (q: string) => void; placeholder: string;
  children?: ReactNode; more?: ReactNode; chips: Chip[]; onRemove: (key: string) => void; onClear: () => void;
  active?: boolean;  // a filter in the bar itself is in use (it has no chip): Clear still shows
}) {
  const [text, setText] = useState(query);
  const [open, setOpen] = useState(false);
  const moreId = `${searchId}-more`;
  // The address changed under us (Back, a cleared search): show what it says
  const shown = useRef(query);
  useEffect(() => {
    if (query !== shown.current) { shown.current = query; setText(query); }
  }, [query]);
  useEffect(() => {
    if (text === query) return;
    const t = window.setTimeout(() => { shown.current = text; onQuery(text); }, 350);
    return () => window.clearTimeout(t);
  }, [text]);  // eslint-disable-line react-hooks/exhaustive-deps
  const filtering = !!query || chips.length > 0 || active;
  return (
    <>
      <form className="report-bar" role="search" aria-label={label} onSubmit={(e) => e.preventDefault()}>
        <div className="report-search-box">
          <label htmlFor={searchId} className="visually-hidden">{placeholder}</label>
          <span className="search-icon" aria-hidden="true">⌕</span>
          <input id={searchId} type="search" value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)} />
        </div>
        {children}
        {more && (
          <button type="button" className={`btn small ${chips.length ? "" : "secondary"}`} aria-expanded={open}
            aria-controls={moreId} onClick={() => setOpen(!open)}>Filters{chips.length ? ` (${chips.length})` : ""}</button>
        )}
      </form>
      {more && open && (
        <div id={moreId} className="report-more">
          {more}
          <button type="button" className="linkbtn small report-more-done" onClick={() => setOpen(false)}>Done</button>
        </div>
      )}
      {filtering && (
        <ul className="report-chips" aria-label="Search and filters in use">
          {chips.map((c) => (
            <li key={c.key} className="chip">{c.text}
              <button type="button" aria-label={`Remove the filter ${c.text}`} onClick={() => onRemove(c.key)}>×</button>
            </li>
          ))}
          <li><button type="button" className="linkbtn small" onClick={() => { setText(""); shown.current = ""; onClear(); }}>
            Clear search and filters</button></li>
        </ul>
      )}
    </>
  );
}

/** A column heading that sorts the list by `sortKey` (the same look on every list): first ascending, then descending.
 * `sort` is the list's current sort ("email" or "-email"). */
export function SortHeading({ label, sortKey, sort, onSort, className }: {
  label: string; sortKey: string; sort: string; onSort: (sort: string) => void; className?: string;
}) {
  const on = sort === sortKey || sort === `-${sortKey}`;
  const desc = sort === `-${sortKey}`;
  return (
    <th scope="col" className={className} aria-sort={on ? (desc ? "descending" : "ascending") : "none"}>
      <button type="button" className="sort-btn" onClick={() => onSort(on && !desc ? `-${sortKey}` : sortKey)}>
        {label}<span aria-hidden="true">{on ? (desc ? " ▼" : " ▲") : ""}</span>
      </button>
    </th>
  );
}
