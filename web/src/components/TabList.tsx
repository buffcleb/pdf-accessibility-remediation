import { useRef, type KeyboardEvent } from "react";
import { fmtNum } from "./ui";
import { tabStep } from "./tabKeys";

/** Accessible tabs: arrow keys, Home and End move between them; only the selected one is in the Tab order. Each tab
 * is `${idPrefix}-tab-${key}` and controls the panel `${idPrefix}-panel-${key}`. */
export function TabList({ label, items, active, onPick, idPrefix, small = false }: {
  label: string; items: { key: string; label: string; count?: number | null }[]; active: string;
  onPick: (key: string) => void; idPrefix: string; small?: boolean;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  function onKey(e: KeyboardEvent<HTMLButtonElement>, i: number) {
    const next = tabStep(e.key, i, items.length);
    if (next === null) return;
    e.preventDefault();
    const t = items[next];
    onPick(t.key);
    refs.current[t.key]?.focus();
  }
  return (
    <div role="tablist" aria-label={label} className={small ? "doc-subtabs" : "doc-tablist"}>
      {items.map((t, i) => (
        <button key={t.key} ref={(el) => { refs.current[t.key] = el; }} type="button" role="tab" id={`${idPrefix}-tab-${t.key}`}
          aria-selected={t.key === active} aria-controls={`${idPrefix}-panel-${t.key}`} tabIndex={t.key === active ? 0 : -1}
          className="doc-tab" onClick={() => onPick(t.key)} onKeyDown={(e) => onKey(e, i)}>
          {t.label}{t.count != null && <span className="doc-count">{fmtNum(t.count)}</span>}
        </button>
      ))}
    </div>
  );
}
