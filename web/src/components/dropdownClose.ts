/* When an open dropdown closes (#569), like any other: a click or tap outside it, Escape, or the focus moving out of
 * it (Tab). Anything inside it, ticking its boxes or clicking between them, keeps it open. */

export type DropdownEvent =
  | { kind: "pointer"; inside: boolean }
  | { kind: "key"; key: string }
  // `to`: where the focus went; null when it went nowhere the page knows (a click on plain text, the browser's own bar)
  | { kind: "focus"; to: "inside" | "outside" | null };

/** Whether the event closes the dropdown, and if so whether the focus goes back to its button. */
export function dropdownClose(e: DropdownEvent): "stay" | "close" | "close-and-focus" {
  if (e.kind === "pointer") return e.inside ? "stay" : "close";
  if (e.kind === "key") return e.key === "Escape" ? "close-and-focus" : "stay";
  // A click inside on no control blurs it with nowhere to go; one outside is the pointer's to close
  return e.to === "outside" ? "close" : "stay";
}
