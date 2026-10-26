import { useEffect, useRef, type ReactNode } from "react";
import { dropdownClose, type DropdownEvent } from "./dropdownClose";

/** A dropdown on <details> (#569): its summary is the button (the browser says whether it's expanded), and it closes
 * on a click outside, Escape (the focus back on the button) or Tab out of it. */
export function Dropdown({ label, className, children }: { label: ReactNode; className?: string; children: ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null);

  function apply(e: DropdownEvent) {
    const el = ref.current;
    const what = dropdownClose(e);
    if (!el || !el.open || what === "stay") return false;
    el.open = false;
    if (what === "close-and-focus") el.querySelector("summary")?.focus();
    return true;
  }

  // Listening all the time, not only once open: a click straight after opening it is caught too
  useEffect(() => {
    const away = (e: PointerEvent) => { apply({ kind: "pointer", inside: !!ref.current?.contains(e.target as Node) }); };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <details ref={ref} className={className}
      onKeyDown={(e) => { if (apply({ kind: "key", key: e.key })) { e.preventDefault(); e.stopPropagation(); } }}
      onBlur={(e) => {
        // A click on its padding focuses what holds it (main, tabIndex -1), if anything: nowhere, not out of it
        const to = e.relatedTarget as Node | null;
        const el = ref.current;
        const where = !to || !el || to.contains(el) ? null : el.contains(to) ? "inside" : "outside";
        apply({ kind: "focus", to: where });
      }}>
      <summary>{label}</summary>
      {children}
    </details>
  );
}
