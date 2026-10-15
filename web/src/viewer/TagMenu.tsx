import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { menuEntries, type MenuItem } from "./menu";

/** The tag editor's menu, opened by a right-click, by Shift+F10 or the Menu key on a tag, or by the panel's
 * Actions button. Its top level is short: the main kinds of change (merge and split, lists, reading order) each
 * open a submenu beside it, by pointing at them, clicking, or Right, Enter or Space. Up and Down move through a
 * menu, Left or Escape closes a submenu, Escape closes the menu and puts the focus back where it was. A choice that
 * can't be made now stays in its menu, dimmed, with the reason under it. */
export function TagMenu({ x, y, title, items, onChoose, onClose }: {
  x: number; y: number; title: string; items: MenuItem[]; onChoose: (item: MenuItem) => void; onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const subRef = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(document.activeElement);
  const [at, setAt] = useState({ left: x, top: y });
  const [open, setOpen] = useState<{ group: MenuItem["group"]; focus: boolean } | null>(null);
  const [subAt, setSubAt] = useState<{ left: number; top: number } | null>(null);
  const sections = menuEntries(items);
  const opened = open ? sections.flat().find((e) => e.kind === "group" && e.group === open.group) : undefined;

  // Keep it on the screen
  useLayoutEffect(() => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    setAt({ left: Math.max(8, Math.min(x, window.innerWidth - box.width - 8)), top: Math.max(8, Math.min(y, window.innerHeight - box.height - 8)) });
  }, [x, y, items.length]);

  // A submenu opens beside its line: to the right, or to the left when there's no room
  useLayoutEffect(() => {
    if (!open) { setSubAt(null); return; }
    const line = ref.current?.querySelector<HTMLElement>(`[data-group="${open.group}"]`)?.getBoundingClientRect();
    const menu = ref.current?.getBoundingClientRect();
    const sub = subRef.current?.getBoundingClientRect();
    if (!line || !menu) return;
    const w = sub?.width ?? 300, h = sub?.height ?? 0;
    const left = menu.right - 4 + w <= window.innerWidth - 8 ? menu.right - 4 : Math.max(8, menu.left - w + 4);
    setSubAt({ left, top: Math.max(8, Math.min(line.top - 6, window.innerHeight - h - 8)) });
  }, [open, at, items.length]);

  const buttons = (box: HTMLElement | null) => [...(box?.querySelectorAll<HTMLButtonElement>(":scope > button[role=menuitem], :scope > div > button[role=menuitem]") ?? [])];
  const firstOf = (box: HTMLElement | null) => (buttons(box).find((b) => b.getAttribute("aria-disabled") !== "true") ?? buttons(box)[0]);
  useEffect(() => {
    firstOf(ref.current)?.focus();
    const away = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!ref.current?.contains(t) && !subRef.current?.contains(t)) onClose();
    };
    document.addEventListener("pointerdown", away, true);
    window.addEventListener("blur", onClose);
    return () => { document.removeEventListener("pointerdown", away, true); window.removeEventListener("blur", onClose); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // Opened from the keyboard: its first choice takes the focus
  useEffect(() => { if (open?.focus && subAt) firstOf(subRef.current)?.focus(); }, [open, subAt]);

  const close = () => { onClose(); (opener.current as HTMLElement | null)?.focus?.(); };
  const choose = (item: MenuItem) => { if (!item.disabled) { onChoose(item); (opener.current as HTMLElement | null)?.focus?.(); } };
  const closeSub = () => {
    const group = open?.group;
    setOpen(null);
    if (group) ref.current?.querySelector<HTMLElement>(`[data-group="${group}"]`)?.focus();
  };
  const move = (box: HTMLElement | null, e: React.KeyboardEvent) => {
    const list = buttons(box);
    const k = list.indexOf(document.activeElement as HTMLButtonElement);
    const to = e.key === "ArrowDown" ? (k + 1) % list.length : e.key === "ArrowUp" ? (k - 1 + list.length) % list.length
      : e.key === "Home" ? 0 : e.key === "End" ? list.length - 1 : -1;
    if (to < 0) return false;
    list[to]?.focus();
    return true;
  };
  const line = (item: MenuItem, top: boolean) => (
    <button key={item.id} type="button" role="menuitem" className="pv-menu-item" aria-disabled={item.disabled ? "true" : undefined}
      onMouseEnter={top ? () => setOpen(null) : undefined} onClick={() => choose(item)}>
      <span>{item.label}</span>
      {/* On the top level only a reason is shown; a submenu has room to say what each choice does */}
      {(item.disabled ?? (top ? undefined : item.note)) && <span className="pv-menu-note">{item.disabled ?? item.note}</span>}
    </button>
  );

  return (
    <>
      <div ref={ref} className="pv-menu" role="menu" aria-label={title} style={at}
        onContextMenu={(e) => e.preventDefault()}
        onKeyDown={(e) => {
          if (move(ref.current, e)) { e.preventDefault(); e.stopPropagation(); return; }
          const group = (document.activeElement as HTMLElement | null)?.dataset.group as MenuItem["group"] | undefined;
          if (group && ["ArrowRight", "Enter", " "].includes(e.key)) { setOpen({ group, focus: true }); e.preventDefault(); e.stopPropagation(); return; }
          if (e.key === "Escape" || e.key === "Tab") { close(); e.preventDefault(); e.stopPropagation(); }
        }}>
        <p className="pv-menu-title small">{title}</p>
        {sections.map((section, n) => (
          <div key={n} role="group" className="pv-menu-group">
            {section.map((entry) => entry.kind === "item" ? line(entry.item, true) : (
              <button key={entry.group} type="button" role="menuitem" className={`pv-menu-item pv-menu-parent${entry.none ? " none" : ""}`}
                data-group={entry.group} aria-haspopup="menu" aria-expanded={open?.group === entry.group}
                onMouseEnter={() => setOpen({ group: entry.group, focus: false })}
                onClick={() => setOpen(open?.group === entry.group ? null : { group: entry.group, focus: false })}>
                <span>{entry.label}</span><span className="pv-menu-arrow" aria-hidden="true">›</span>
              </button>
            ))}
          </div>
        ))}
      </div>
      {opened?.kind === "group" && (
        <div ref={subRef} className="pv-menu pv-submenu" role="menu" aria-label={opened.label}
          style={subAt ?? { left: -9999, top: 0 }}
          onContextMenu={(e) => e.preventDefault()}
          onKeyDown={(e) => {
            if (move(subRef.current, e)) { e.preventDefault(); e.stopPropagation(); return; }
            if (e.key === "ArrowLeft" || e.key === "Escape") { closeSub(); e.preventDefault(); e.stopPropagation(); return; }
            if (e.key === "Tab") { close(); e.preventDefault(); e.stopPropagation(); }
          }}>
          {opened.items.map((item) => line(item, false))}
        </div>
      )}
    </>
  );
}
