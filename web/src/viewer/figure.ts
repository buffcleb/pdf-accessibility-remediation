/** Splitting a figure that holds several drawings (a scheme, a figure with panels): its pieces of content, the part
 * of the page they cover, and the edits that make chosen pieces tags of their own. Kept apart from the dialog so it
 * can be tested. */
import type { TagEdit, TagElement, TagMap, TagPiece } from "../api";

export const pieceKey = (p: { page: number; mcid: number }) => `${p.page}:${p.mcid}`;

/** The tag's own pieces of content on the page it starts on (that's the part a figure split shows). */
export function figurePieces(map: TagMap, el: TagElement): TagPiece[] {
  const mine = (map.pieces ?? []).filter((p) => p.el === el.i);
  const page = Math.min(...mine.map((p) => p.page));
  return mine.filter((p) => p.page === page);
}

/** The part of the page to show: round all the pieces, with a little room. */
export function figureFrame(pieces: TagPiece[], pageW: number, pageH: number, room = 6): [number, number, number, number] {
  const x0 = Math.min(...pieces.map((p) => p.bbox[0])), y0 = Math.min(...pieces.map((p) => p.bbox[1]));
  const x1 = Math.max(...pieces.map((p) => p.bbox[2])), y1 = Math.max(...pieces.map((p) => p.bbox[3]));
  return [Math.max(0, x0 - room), Math.max(0, y0 - room), Math.min(pageW, x1 + room), Math.min(pageH, y1 + room)];
}

/** One new tag to make, as this type with this alt text: these whole pieces, or (`box`, page units) exactly what's
 * inside a box drawn over the figure, cutting through pieces that cross its edge. */
export type PieceGroup = { keys: string[]; role: string; alt: string; box?: [number, number, number, number] };

export const NEEDS_ALT = ["Figure", "Formula"];

/** Why a group can't be made yet, or null. `taken` are pieces already in other groups. */
export function groupProblem(all: TagPiece[], taken: Set<string>, chosen: Set<string>, role: string, alt: string, box: boolean = false): string | null {
  if (box) return NEEDS_ALT.includes(role) && !alt.trim() ? `A ${role === "Figure" ? "figure" : "formula"} needs alt text describing it.` : null;
  if (!chosen.size) return "Click the pieces that belong together first.";
  if (all.filter((p) => !taken.has(pieceKey(p)) && !chosen.has(pieceKey(p))).length === 0)
    return "Leave at least one piece in this figure (change the figure itself to change all of it).";
  if (NEEDS_ALT.includes(role) && !alt.trim()) return `A ${role === "Figure" ? "figure" : "formula"} needs alt text describing it.`;
  return null;
}

/** The edits: one take_pieces per group, in order (each new tag is read after the figure, so the last made comes
 * first), and new alt text for what stays when it changed. */
export function figureEdits(el: TagElement, all: TagPiece[], groups: PieceGroup[], keepAlt: string, page = all[0]?.page ?? 1): TagEdit[] {
  const byKey = new Map(all.map((p) => [pieceKey(p), p]));
  const alt = (g: PieceGroup) => (g.alt.trim() ? { alt: g.alt.trim() } : {});
  const made = [...groups].reverse().map((g): TagEdit => (g.box
    ? { op: "cut_area", element: el.i, expect_role: el.role, role: g.role, page, bbox: g.box, ...alt(g) }
    : { op: "take_pieces", element: el.i, expect_role: el.role, role: g.role,
      pieces: g.keys.map((k) => byKey.get(k)!).filter(Boolean).map((p) => ({ page: p.page, mcid: p.mcid })), ...alt(g) }));
  // Whole pieces go first: a cut renumbers the pieces it goes through, which a later take couldn't find
  const out: TagEdit[] = [...made.filter((o) => o.op === "take_pieces"), ...made.filter((o) => o.op === "cut_area")];
  if (keepAlt.trim() !== (el.alt ?? "").trim() && keepAlt.trim()) out.push({ op: "set_alt", element: el.i, expect_role: el.role, alt: keepAlt.trim() });
  return out;
}

const area = (b: [number, number, number, number]) => Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]);

/** The piece a click at a point (page units) means: the smallest one under it, so a piece inside a bigger one can be
 * clicked; the bigger one is clicked where nothing smaller covers it. Pieces in `skip` aren't counted. */
export function pieceAt(pieces: TagPiece[], x: number, y: number, skip: Set<string> = new Set(), slack = 1): string | null {
  let best: TagPiece | null = null;
  for (const p of pieces) {
    const [x0, y0, x1, y1] = p.bbox;
    if (skip.has(pieceKey(p)) || x < x0 - slack || x > x1 + slack || y < y0 - slack || y > y1 + slack) continue;
    if (!best || area(p.bbox) < area(best.bbox)) best = p;
  }
  return best ? pieceKey(best) : null;
}

/** The pieces wholly inside a box drawn over the figure (page units, either corner first), but `skip`. */
export function piecesIn(pieces: TagPiece[], box: [number, number, number, number], skip: Set<string> = new Set(), slack = 1): string[] {
  const [x0, x1] = [Math.min(box[0], box[2]), Math.max(box[0], box[2])];
  const [y0, y1] = [Math.min(box[1], box[3]), Math.max(box[1], box[3])];
  return pieces.filter((p) => !skip.has(pieceKey(p)) && p.bbox[0] >= x0 - slack && p.bbox[1] >= y0 - slack
    && p.bbox[2] <= x1 + slack && p.bbox[3] <= y1 + slack).map(pieceKey);
}

/** Pieces in drawing order for the picture: biggest first, so a smaller one's outline is drawn over a bigger one's. */
export function bySize(pieces: TagPiece[]): TagPiece[] {
  return [...pieces].sort((a, b) => area(b.bbox) - area(a.bbox));
}

/** A tag a pending split will make, for the viewer to draw before it's saved: its type, alt text, and where it is. */
export type SplitPart = { role: string; alt: string; boxes: { page: number; bbox: [number, number, number, number] }[] };

/** The tags pending splits (take_pieces, cut_area) will make, by the tag they come out of, in the order they'll be
 * read: each is placed right after that tag, so the last one made is read first. */
export function splitPreview(map: TagMap, ops: TagEdit[]): Map<number, SplitPart[]> {
  const out = new Map<number, SplitPart[]>();
  const byKey = new Map((map.pieces ?? []).map((p) => [pieceKey(p), p]));
  for (const op of ops) {
    if (op.op !== "take_pieces" && op.op !== "cut_area") continue;
    let boxes: SplitPart["boxes"];
    if (op.op === "cut_area") boxes = [{ page: op.page, bbox: op.bbox }];
    else {
      const pages = new Map<number, [number, number, number, number]>();
      for (const k of op.pieces) {
        const p = byKey.get(pieceKey(k));
        if (!p) continue;
        const u = pages.get(p.page);
        pages.set(p.page, u ? [Math.min(u[0], p.bbox[0]), Math.min(u[1], p.bbox[1]), Math.max(u[2], p.bbox[2]), Math.max(u[3], p.bbox[3])] : [...p.bbox]);
      }
      boxes = [...pages].map(([page, bbox]) => ({ page, bbox }));
    }
    out.set(op.element, [{ role: op.role, alt: op.alt ?? "", boxes }, ...(out.get(op.element) ?? [])]);
  }
  return out;
}
