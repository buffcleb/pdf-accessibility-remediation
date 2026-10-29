/** The Areas window's tabs: the tagged content in the area (moved into a tag), a new tag from what's untagged in it
 * (or from a part of a picture), and Suggest tags with AI. */
export type AreaTab = "tagged" | "new" | "ai";

/** The tab an area opens on: its tagged content, unless there's none and there's something to make a new tag from
 * (`untagged`: its untagged items, and a picture it's drawn over). Never the AI's, which costs a call. */
export function areaTab(tagged: number, untagged: number): AreaTab {
  return tagged === 0 && untagged > 0 ? "new" : "tagged";
}
