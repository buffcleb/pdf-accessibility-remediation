import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  SIDE_DEFAULT, SIDE_MAX, SIDE_MIN, SPLIT_DEFAULT, SPLIT_MAX, SPLIT_MIN, loadSide, loadSplit, saveSide, saveSplit, sideAt, sideKey,
  splitAt, splitKey,
} from "./split";
import { reportLabel, reportLink } from "./report";
import type { BackState } from "../pages/JobDetail";
import { get, post, put, type AreaTags, type DocVersion, type Job, type ServiceChoice, type TagDraft, type TagDraftInfo, type TagDrafts, type TagEdit, type TagElement, type Versions } from "../api";
import { useAuth } from "../auth";
import { ErrorNotice, LiveRegion, Loading, Notice, fmtDate, useLoad, useTitle } from "../components/ui";
import { useConfirmLeave, useUnsavedChanges } from "../components/unsaved";
import { MarksContext, useFindingMarks } from "../components/reviewMarks";
import type { Area, AreaBoxes } from "./draft";
import { DeleteTag, DeleteTags, EditPanel, ReviewStatus, SaveChanges } from "./EditPanel";
import { Reader } from "./Reader";
import { LONG_DOCUMENT_PAGES, ROLE_NAMES, addEdit, describe, draftTree, jumpQuestion, undoMoves } from "./draft";
import { fresh, historyKey, push, redo, undo, type History } from "./history";
import { PAGE_TOP, Pane, TREE, VersionPicker, type OverlayMode, type PaneLayout } from "./Pane";
import { TreePane } from "./TreePane";
import { TagMenu } from "./TagMenu";
import { centered } from "./scroll";
import { typedPage } from "./pageBox";
import { isolateItem } from "./isolate";
import { inReadingOrder, menuAndTypes, pageMenu, pick, tagMenu, type MenuItem, type MenuPoint } from "./menu";
import { ListBuilder } from "./ListBuilder";
import { TypeDialog } from "./TypeDialog";
import { TagDialog } from "./TagDialog";
import { FigureSplit } from "./FigureSplit";
import { SplitDialog } from "./SplitDialog";
import { AreaDialog } from "./AreaDialog";
import { HeadingDialog } from "./ChangesDialog";
import { DocumentPanel } from "./DocumentPanel";
import { TableDialog } from "./TableDialog";
import { clearVersionCache, type PaneData } from "./pdf";
import { findItem, spotExtent, spotFor, spotWords, type Spot } from "./spot";
import { expand, store, takeBack, withIds } from "./made";
import { changePending, justMade, makesTag } from "./pending";
import { PendingTag } from "./PendingTag";

// 1 = fit the page to the pane's width; larger values scroll sideways
const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3];

const MODES: { value: OverlayMode; label: string }[] = [
  { value: "flagged", label: "Flagged tags" },
  { value: "all", label: "All tags" },
  { value: "order", label: "Reading order" },
  { value: "off", label: "No overlay" },
];

function pending(v: DocVersion) {
  return v.status === "queued" || v.status === "submitted" || v.status === "processing" || !!v.checking
    || v.review_status === "queued" || v.review_status === "running";
}

const ALONE = { of: -1, ids: [] as number[] }; // no other tag selected along with the selected one
const NONE: number[] = []; // one empty list, so the panes don't see a new one each time

export function TagViewer() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  // Where "Back" goes: the exact view this was opened from (kept, since the viewer changes its own address)
  const location = useLocation();
  const [backTo] = useState(() => (location.state as BackState | null)?.back ?? `/jobs/${id}`);
  const { me } = useAuth();
  const job = useLoad(() => get<Job>(`/jobs/${id}`), [id]);
  const versions = useLoad(() => get<Versions>(`/jobs/${id}/versions`), [id],
    useCallback((v: Versions) => v.versions.some(pending), []));
  useTitle(job.data ? `Tag manager: ${job.data.filename}` : "Tag manager");

  const list = versions.data?.versions ?? [];
  const usable = list.filter((v) => v.status === "ready" && v.has_tagmap);
  const [left, setLeft] = useState<number | null>(null);
  const [right, setRight] = useState<number | null>(null);
  const [mode, setMode] = useState<OverlayMode>("flagged");
  const [sync, setSync] = useState(true);
  const [zoom, setZoomState] = useState<number>(() => {
    try { return Number(localStorage.getItem("pv-zoom")) || 1; } catch { return 1; }
  });
  // Tags' cards on hover and focus: turned off, they stay off for the rest of this editing session (this tab)
  const [cards, setCardsState] = useState<boolean>(() => {
    try { return sessionStorage.getItem("pv-cards") !== "off"; } catch { return true; }
  });
  const setCards = (on: boolean) => {
    setCardsState(on);
    try { sessionStorage.setItem("pv-cards", on ? "on" : "off"); } catch { /* this tab's preference only */ }
  };
  const [selected, setSelected] = useState<{ side: "left" | "right"; i: number } | null>(null);
  // Other tags selected along with `selected` (Ctrl or Cmd-click, Shift-click). They belong to that tag: once
  // another tag is selected on its own they no longer count
  const [also, setAlso] = useState<{ of: number; ids: number[] }>(ALONE);
  const [menu, setMenu] = useState<{ x: number; y: number; ids: number[]; at: MenuPoint | null; page?: number } | null>(null);
  const navigate = useNavigate();
  // The unsaved changes, with their history for undo and redo (#365). Loading a draft or saving starts afresh.
  // They're kept with the new tags they make named (made.ts, #692): `stored` is what's saved, `ops` what the tag manager
  // works on, every tag a number of `rightMap` (the right-hand version's tags with the new ones in it)
  const [history, setHistory] = useState<History<TagEdit[]>>(() => fresh([]));
  const stored = history.present;
  const [rightData, setRightData] = useState<PaneData | null>(null);
  const draft = useMemo(() => (rightData ? expand(rightData.tagmap, stored) : null), [rightData, stored]);
  const ops = draft?.ops ?? stored;
  const rightMap = draft?.map ?? null;
  // Taking out an edit that makes a tag takes the changes made to that tag with it (Undo brings them all back)
  const tookToo = (n: number) => setAnnounce(`…and ${n} change${n === 1 ? "" : "s"} made to it. Undo brings them back.`);
  const setOps = useCallback((next: TagEdit[]) => {
    const kept = draft ? store(draft, next) : { ops: withIds(next), dropped: 0 };
    setHistory((h) => push(h, kept.ops));
    if (kept.dropped) tookToo(kept.dropped);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);
  const setStored = useCallback((next: TagEdit[]) => setHistory((h) => push(h, next)), []);
  const resetOps = useCallback((next: TagEdit[]) => setHistory(fresh(withIds(next))), []);
  const [rightPage, setRightPage] = useState(1);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [waitingFor, setWaitingFor] = useState<number | null>(null);
  // The changes a save sent, still shown while it's checked: the tree and the page stay as they were edited, not
  // back to the version they were made on, until the new version opens (or the save fails)
  const [sent, setSent] = useState<TagEdit[] | null>(null);
  const [announce, setAnnounce] = useState("");
  const [failedSave, setFailedSave] = useState<DocVersion | null>(null);
  // Either pane can show the tag tree of the other pane's version instead of a document
  // The left pane starts as the tag tree of the right-hand version: what most people open the viewer for. A link
  // that asks for a version on the left ("?left=") gets that document instead, and the pane's picker switches.
  const [split, setSplit] = useState(() => loadSplit(localStore()));
  const [sideWidth, setSideWidth] = useState(() => loadSide(localStore()));
  const gridRef = useRef<HTMLDivElement>(null);
  // The heading stays in view under the site's header: what sits below it is told its height
  // (kept as state, not a ref: the heading isn't there on the first render, while the document loads)
  const [topEl, setTopEl] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!topEl) return;
    const root = document.documentElement.style;
    const note = () => root.setProperty("--pv-top-h", `${Math.round(topEl.getBoundingClientRect().height)}px`);
    note();
    const seen = new ResizeObserver(note);
    seen.observe(topEl);
    return () => { seen.disconnect(); root.removeProperty("--pv-top-h"); };
  }, [topEl]);
  const [tree, setTree] = useState<"left" | "right" | null>(() => (params.get("left") === null ? "left" : null));
  const [hover, setHover] = useState<{ side: "left" | "right"; i: number } | null>(null);
  // Where the document next to the tree is scrolled to (page, y in page units), so the tree follows it
  const [follow, setFollow] = useState<{ page: number; y: number } | null>(null);
  const [jump, setJump] = useState(0);
  const [reviewBusy, setReviewBusy] = useState(false);
  // An area chosen on the right-hand page, for changing which content tags cover
  const [area, setArea] = useState<Area | null>(null);
  const [areaBoxes, setAreaBoxes] = useState<AreaBoxes | null>(null); // the AI's figures for the area, on the page
  const [areaMode, setAreaMode] = useState(false);
  useEffect(() => {
    if (!areaMode) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setAreaMode(false); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [areaMode]);
  const [reading, setReading] = useState(false);
  const [readFrom, setReadFrom] = useState<{ i: number; n: number } | null>(null); // "Read aloud from here"
  // All pages in one scroll (the default), or one page at a time with previous and next
  const [view, setViewState] = useState<"all" | "one">("all");
  // A long document opens one page at a time (once, when its length is known; the person's own choice stands after)
  const pages = job.data?.pages ?? 0;
  const long = pages > LONG_DOCUMENT_PAGES;
  const viewChosen = useRef(false);
  useEffect(() => {
    if (long && !viewChosen.current) { viewChosen.current = true; setViewState("one"); }
  }, [long]);
  const [askAll, setAskAll] = useState(false);
  const [askJump, setAskJump] = useState<{ side: "left" | "right"; e: TagElement; first: number; last: number; pages: number; here: boolean } | null>(null);
  const [onePage, setOnePage] = useState(1);
  const onePageRef = useRef<number | null>(null);
  onePageRef.current = view === "one" ? onePage : null;
  const followFrame = useRef(0);
  const quietFollow = useRef(0); // until then, a scroll of the document doesn't move the tree

  // Default panes: the newest reviewed version (or the remediated file) on the right, its tag tree on the left.
  // The original is what the left pane shows when the tree is switched to a document.
  useEffect(() => {
    if (!usable.length) return;
    if (left === null) setLeft(Number(params.get("left") ?? usable[0].number));
    if (right === null) {
      const wanted = params.get("v") ?? params.get("right");
      // The newest saved version; else the Adobe route's improved copy, the document people get (#499); else the result
      const fallback = [...usable].reverse().find((v) => v.kind === "reviewed" || v.kind === "corrected")
        ?? usable.find((v) => v.kind === "enhanced") ?? usable.find((v) => v.kind === "primary") ?? usable[usable.length - 1];
      setRight(wanted !== null && usable.some((v) => v.number === Number(wanted)) ? Number(wanted) : fallback.number);
    }
  }, [usable, left, right, params]);

  useEffect(() => () => clearVersionCache(), []);

  // --- unsaved edits are kept on the server (a long review takes days) ------------------------------------------
  // null: not looked up yet. After that, edits are sent a moment after each change, so closing the tab loses nothing
  const [draftReady, setDraftReady] = useState(false);
  const [restored, setRestored] = useState<{ at: string; count: number; from?: string } | null>(null);
  // A save the engine refused: its changes can be put back as unsaved changes, on the version they were made on, to
  // save again once what refused them is fixed (or after taking out the change that was refused)
  const [retried, setRetried] = useState<{ label: string; count: number } | null>(null);
  const [notAgain, setNotAgain] = useState<number | null>(null);  // the failed save whose notice was dealt with
  const newest = list.reduce<DocVersion | null>((m, v) => (!m || v.number > m.number ? v : m), null);
  const failedNow = failedSave ?? (newest?.status === "failed" && newest.kind === "reviewed" ? newest : null);
  const failed = failedNow && failedNow.number !== notAgain ? failedNow : null;
  async function tryAgain(v: DocVersion) {
    try {
      const got = await get<{ based_on: number | null; edits: TagEdit[] }>(`/jobs/${id}/versions/${v.number}/edits`);
      if (got.based_on === null || !usable.some((x) => x.number === got.based_on && x.kind !== "original")) {
        throw new Error("The version its changes were made on has changed since, so they can't be put back on it.");
      }
      setRight(got.based_on);
      resetOps(got.edits);
      setSelected(null);
      setFailedSave(null);
      setNotAgain(v.number);
      setRetried({ label: v.label, count: got.edits.length });
      setAnnounce(`The changes of ${v.label} are back as unsaved changes.`);
    } catch (e) { setSaveError(e); }
  }
  const [lostDraft, setLostDraft] = useState<TagDraft | null>(null);   // its version is gone: can't be applied
  const [otherDraft, setOtherDraft] = useState<TagDraftInfo | null>(null);
  const [readOnlyFor, setReadOnlyFor] = useState<TagDraftInfo | null>(null);
  const [notesOpen, setNotesOpen] = useState(false);  // the notes under the heading, folded at first
  const [kept, setKept] = useState<{ at: Date | null; dirty: boolean; error: boolean }>({ at: null, dirty: false, error: false });
  const hasDraft = useRef(false);
  const lastKept = useRef("");
  const canEditDoc = !!versions.data?.can_edit;
  useEffect(() => {
    if (draftReady || !id || !versions.data || right === null) return;
    if (!canEditDoc) { setDraftReady(true); return; }
    let live = true;
    get<TagDrafts>(`/jobs/${id}/tag-draft`).then((d) => {
      if (!live) return;
      if (d.mine) {
        hasDraft.current = true;
        if (usable.some((v) => v.number === d.mine!.based_on && v.kind !== "original")) {
          lastKept.current = JSON.stringify([d.mine.based_on, d.mine.edits]);
          setRight(d.mine.based_on);
          resetOps(d.mine.edits);
          setRestored({ at: d.mine.updated_at, count: d.mine.edits.length });
        } else setLostDraft(d.mine);
      } else if (d.others.length) setOtherDraft(d.others[0]);
      setDraftReady(true);
    }).catch(() => { if (live) setDraftReady(true); });   // no draft service: edit as before, in this tab only
    return () => { live = false; };
  }, [draftReady, id, versions.data, right, canEditDoc, usable]);
  useEffect(() => {
    if (!draftReady || !canEditDoc || !id || right === null || readOnlyFor || lostDraft) return;
    if (!stored.length && !hasDraft.current) return;
    const body = JSON.stringify([right, stored]);
    if (body === lastKept.current) return;
    setKept((k) => ({ ...k, dirty: true }));
    const timer = window.setTimeout(() => {
      put<TagDraft | null>(`/jobs/${id}/tag-draft`, { based_on: right, edits: stored }).then(() => {
        lastKept.current = body;
        hasDraft.current = stored.length > 0;
        setKept({ at: stored.length ? new Date() : null, dirty: false, error: false });
      }).catch(() => setKept((k) => ({ ...k, dirty: true, error: true })));
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [stored, right, draftReady, canEditDoc, id, readOnlyFor, lostDraft]);
  async function startFrom(other: TagDraftInfo) {
    setOtherDraft(null);
    try {
      const d = await get<TagDraft>(`/jobs/${id}/tag-draft/${other.user_id}`);
      if (!usable.some((v) => v.number === d.based_on && v.kind !== "original")) throw new Error("The version those changes were made on is no longer available.");
      setRight(d.based_on);
      resetOps(d.edits);
      setRestored({ at: d.updated_at, count: d.edits.length, from: d.user_email });
    } catch (e) { setSaveError(e); }
  }

  // Ask before leaving only with edits that haven't reached the server yet
  useUnsavedChanges(ops.length > 0 && (kept.dirty || !canEditDoc));
  const confirmLeave = useConfirmLeave();

  // When a save finishes, switch the right pane to the new version
  useEffect(() => {
    if (waitingFor === null) return;
    const v = list.find((x) => x.number === waitingFor);
    if (!v) return;
    if (v.status === "ready") {
      setAnnounce(v.checking ? `${v.label} is saved and open; it's still being checked.` : `${v.label} is ready.`);
      setRight(v.number);
      setSelected(null);
      setWaitingFor(null);
      setSent(null);
    } else if (v.status === "failed") {
      setAnnounce(`Saving ${v.label} failed.`);
      setFailedSave(v);
      setWaitingFor(null);
      setSent(null);
    }
  }, [list, waitingFor]);

  // --- scroll sync ---------------------------------------------------------------------------
  const scrollEls = useRef<Record<string, HTMLDivElement | null>>({});
  const layouts = useRef<Record<string, PaneLayout>>({});
  // Where we last scrolled each pane to ourselves, so its echo scroll event isn't synced back
  const expected = useRef<Record<string, number | null>>({});

  /** Where a page starts in its pane: its place in the scroll, or the top in one-page view. */
  const pageTop = (l: PaneLayout, page: number) => (l.only !== null ? PAGE_TOP : l.offsets[page]);

  const position = (side: string) => {
    const el = scrollEls.current[side];
    const l = layouts.current[side];
    if (!el || !l || !l.offsets.length) return null;
    const y = el.scrollTop;
    let page = 0;
    if (l.only !== null) page = l.only - 1;
    else while (page + 1 < l.offsets.length && l.offsets[page + 1] <= y) page++;
    return { page, frac: Math.min(1, Math.max(0, (y - pageTop(l, page)) / l.heights[page])) };
  };

  /** The page taking up most of the pane: the one the editing panel is about (not one scrolled almost out of view). */
  const mostVisible = (side: string) => {
    const el = scrollEls.current[side];
    const l = layouts.current[side];
    if (!el || !l || !l.offsets.length) return null;
    if (l.only !== null) return l.only;
    const top = el.scrollTop, bottom = top + el.clientHeight;
    let best = 0, bestSeen = -1;
    l.offsets.forEach((o, p) => {
      const seen = Math.min(bottom, o + l.heights[p]) - Math.max(top, o);
      if (seen > bestSeen) { best = p; bestSeen = seen; }
    });
    return best + 1;
  };

  const onScroll = (side: "left" | "right") => () => {
    const self = scrollEls.current[side];
    const pos = position(side);
    if (side === "right") {
      const page = mostVisible("right");
      if (page) setRightPage(page);
    }
    if (pos && tree && tree !== side && performance.now() > quietFollow.current) {
      const l = layouts.current[side];
      window.cancelAnimationFrame(followFrame.current);
      followFrame.current = window.requestAnimationFrame(() =>
        setFollow({ page: pos.page + 1, y: l ? (pos.frac * l.heights[pos.page]) / l.scale : 0 }));
    }
    const echo = expected.current[side];
    if (echo != null && self && Math.abs(self.scrollTop - echo) < 2) {
      expected.current[side] = null; // our own programmatic scroll arriving: don't bounce it back
      return;
    }
    expected.current[side] = null;
    if (!sync || !pos) return;
    const other = side === "left" ? "right" : "left";
    const el = scrollEls.current[other];
    const l = layouts.current[other];
    if (!el || !l || !l.offsets.length) return;
    const page = Math.min(pos.page, l.offsets.length - 1);
    const target = Math.round(pageTop(l, page) + pos.frac * l.heights[page]);
    if (Math.abs(el.scrollTop - target) < 2) return;
    expected.current[other] = target;
    el.scrollTop = target;
  };

  // Zoom keeps both panes on the same spot of the page
  function setZoom(z: number) {
    const keep = { left: position("left"), right: position("right") };
    setZoomState(z);
    try { localStorage.setItem("pv-zoom", String(z)); } catch { /* per-viewer preference only */ }
    window.setTimeout(() => {
      for (const side of ["left", "right"] as const) {
        const pos = keep[side];
        const el = scrollEls.current[side];
        const l = layouts.current[side];
        if (!pos || !el || !l?.offsets[pos.page]) continue;
        const target = Math.round(pageTop(l, pos.page) + pos.frac * l.heights[pos.page]);
        expected.current[side] = target;
        el.scrollTop = target;
      }
    }, 50);
  }

  /** Run once the page is the one shown: at once, or after one-page view has turned to it. */
  const onPageShown = useCallback((page: number, then: () => void) => {
    if (onePageRef.current !== null && onePageRef.current !== page) {
      setOnePage(page);
      window.setTimeout(then, 80);
    } else then();
  }, []);

  // Scroll a pane to a place on a page: to a tag (y to y1, in page units), which is put in the middle of the pane
  // as far as the document's ends allow; or to y near the top. The tree beside it doesn't follow this scroll: it
  // has just shown the tag itself
  const scrollTo = useCallback((side: "left" | "right", page: number, y = 0, y1?: number) => {
    onPageShown(page, () => {
      const el = scrollEls.current[side];
      const l = layouts.current[side];
      if (!el || !l || l.offsets[page - 1] === undefined) return;
      if (y1 !== undefined) quietFollow.current = performance.now() + 500;
      el.scrollTop = y1 === undefined ? pageTop(l, page - 1) + y * l.scale - 60
        : centered(pageTop(l, page - 1) + y * l.scale, (y1 - y) * l.scale, el.clientHeight);
    });
  }, [onPageShown]);

  // Read aloud: outline the tag being read, and scroll only when it's out of view
  const showRead = useCallback((side: "left" | "right", i: number, page: number, y: number) => {
    setSelected({ side, i });
    setAlso(ALONE);
    if (side === "right") setRightPage(page);
    onPageShown(page, () => {
      const el = scrollEls.current[side];
      const l = layouts.current[side];
      if (!el || !l || l.offsets[page - 1] === undefined) return;
      const top = pageTop(l, page - 1) + y * l.scale;
      if (top < el.scrollTop + 20 || top > el.scrollTop + el.clientHeight - 60) el.scrollTop = top - 60;
    });
  }, [onPageShown]);

  // Review items' statuses (#663): signed off here, where they're fixed, as on Needs review
  const marks = useFindingMarks(id);

  // A review item's area, highlighted on its page (#426): from Show on the review list, or the page's own list
  const [spot, setSpot] = useState<Spot | null>(null);
  const showSpot = useCallback((s: Spot) => {
    setSpot(s);
    setAnnounce(spotWords(s));
    setRightPage(s.page);
    const at = spotExtent(s);
    window.setTimeout(() => scrollTo("right", s.page, at?.[1] ?? 0, at?.[3]), 100);
  }, [scrollTo]);
  const closeSpot = useCallback(() => {
    setSpot(null);
    setAnnounce("Highlight closed.");
    scrollEls.current.right?.focus({ preventScroll: true }); // the label had focus: back to the pages
  }, []);

  // Jump to ?el= and ?page= (from Show on the review list) once the right pane has loaded: the item's tag selected,
  // and the area it's about highlighted
  const jumped = useRef(false);
  useEffect(() => {
    const el = params.get("el");
    const page = params.get("page");
    if (jumped.current || (el === null && page === null) || !rightMap) return;
    jumped.current = true;
    const e = el === null ? undefined : rightMap.elements[Number(el)];
    if (e) setSelected({ side: "right", i: e.i });
    const findings = list.find((v) => v.number === right)?.summary?.findings ?? [];
    const item = findItem(findings, params.get("f"), page === null ? null : Number(page),
      e ? e.i : null);
    const n = page === null ? null : Number(page);
    const s = item ? spotFor(item, rightMap)
      : n !== null && rightMap.pages[n - 1] ? { page: n, boxes: [], issue: `Page ${n}` } : null;
    if (s && (item || !e)) showSpot(s);
    else if (e?.boxes.length) {
      const b = e.boxes[0];
      window.setTimeout(() => scrollTo("right", b.page, b.bbox[1], b.bbox[3]), 100);
    }
  }, [params, rightMap, list, right, scrollTo, showSpot]);

  const selectFrom = (side: "left" | "right") => (i: number | null) => {
    setSelected(i === null ? null : { side, i });
    setAlso(ALONE); // or coming back to a tag later would bring back the tags once selected with it
    if (i !== null && side === "right" && rightMap) {
      const e = rightMap.elements[i];
      if (e?.boxes.length) setRightPage(e.boxes[0].page);
    }
  };

  // Clicking a tag in the tree selects it in the pane showing that version and scrolls to it
  const showFromTree = (docSide: "left" | "right") => (e: TagElement, sure = false) => {
    // One page at a time: a tag that spans pages and starts on another one would turn the page. Ask first
    const q = !sure && onePageRef.current !== null ? jumpQuestion(e, onePageRef.current) : null;
    if (q) { setAskJump({ side: docSide, e, ...q }); return; }
    selectFrom(docSide)(e.i);
    if (docSide === "right") setJump((n) => n + 1);
    if (e.boxes.length) scrollTo(docSide, e.boxes[0].page, e.boxes[0].bbox[1], e.boxes[0].bbox[3]);
  };

  // --- several tags at once, and the menu -----------------------------------------------------
  const canReport = !!job.data && !!me && job.data.user_id === me.id && me.can_submit; // as for the heading's button
  const picked = selected?.side === "right" && also.of === selected.i ? also.ids : NONE;
  const chosen = selected?.side === "right" ? [selected.i, ...picked] : [];
  const pickRight = (i: number, how: "toggle" | "range", visible: number[] = []) => {
    const next = pick(chosen, i, how, visible);
    setSelected({ side: "right", i: next[0] });
    setAlso({ of: next[0], ids: next.slice(1) });
    setAnnounce(next.length > 1 ? `${next.length} tags selected.` : "1 tag selected.");
  };
  // The menu is for the selection when it's opened on one of its tags, and for the tag alone otherwise
  const openMenu = (i: number, x: number, y: number, at: MenuPoint | null = null) => {
    const ids = chosen.includes(i) ? chosen : [i];
    if (!chosen.includes(i)) selectFrom("right")(i);
    setMenu({ x, y, ids, at });
  };
  const [isolated, setIsolated] = useState<number | null>(null); // a tag of the right-hand version, everything else veiled
  const [buildFrom, setBuildFrom] = useState<number[] | null>(null); // the tags whose lines the list builder has open
  // The table editor (#370): a table, or text tags being made one
  const [tableFor, setTableFor] = useState<{ table?: number; tags?: number[] } | null>(null);
  const [typesFor, setTypesFor] = useState<number[] | null>(null);
  const [editFor, setEditFor] = useState<number | null>(null); // the tag open in the "Edit tag" dialog
  const [splitFor, setSplitFor] = useState<number | null>(null); // the tag open in the "Split" dialog
  const [areaOpen, setAreaOpen] = useState(false); // the chosen area's contents, in a dialog
  const [headingDialog, setHeadingDialog] = useState<"changes" | "review" | "document" | null>(null); // opened from the heading's buttons
  const [deleteFor, setDeleteFor] = useState<number | null>(null);
  const [deleteMany, setDeleteMany] = useState<number[] | null>(null); // several tags chosen together
  const [figureFor, setFigureFor] = useState<{ i: number; keys?: string[] } | null>(null); // the figure being split, and pieces chosen already // the tag the "remove or delete" question is about
  // The editing panel: hidden unless asked for, since the menu and its dialogs do its work (remembered per browser)
  const [panel, setPanelState] = useState(() => { try { return localStorage.getItem("pv-panel") === "1"; } catch { return false; } });
  const setPanel = (on: boolean) => {
    setPanelState(on);
    try { localStorage.setItem("pv-panel", on ? "1" : "0"); } catch { /* per-viewer preference only */ }
  }; // the tags whose type the dialog is changing
  const choose = (item: MenuItem) => {
    const first = menu?.ids[0];
    if (item.act === "types" && menu) setTypesFor(menu.ids);
    if (item.act === "edit" && menu?.ids.length) setEditFor(menu.ids[0]);
    if (item.act === "figure-split" && menu?.ids.length) setFigureFor({ i: menu.ids[0] });
    if (item.act === "build" && menu) setBuildFrom(menu.ids);
    if (item.act === "make-table" && menu) setTableFor({ tags: menu.ids });
    if (item.act === "table" && menu?.ids.length && rightMap) {
      let t: number | null = menu.ids[0];
      while (t !== null && rightMap.elements[t]?.role !== "Table") t = rightMap.elements[t]?.parent ?? null;
      if (t !== null) setTableFor({ table: rightMap.elements[t]?.of ?? t });  // a table being rebuilt: the one in the file
    }
    if (item.act === "isolate" && menu?.ids.length) { setIsolated(menu.ids[0]); setAnnounce("Tag isolated: the rest is veiled. Show everything again from the menu or the button at the top."); return setMenu(null); }
    if (item.act === "unisolate") { setIsolated(null); setAnnounce("Showing everything."); return setMenu(null); }
    setMenu(null);
    if (item.act?.startsWith("area-")) {
      const page = menu?.page ?? rightPage;
      const size = rightMap?.pages[page - 1];
      setRightPage(page);
      // What's in the area opens in a dialog: at once for a whole page, once it's drawn for an area
      if (item.act === "area-clear") setArea(null);
      else if (item.act === "area-draw") setAreaMode(!areaMode);
      else if (size) { setArea({ page, bbox: [0, 0, size.w, size.h] }); setAreaMode(false); setAreaOpen(true); }
      setAnnounce(item.act === "area-draw" && !areaMode ? "Drag on the page to draw the area. Escape stops." : `${item.label}: done.`);
      return;
    }
    if (item.id === "merge") setAlso(ALONE); // the others are part of the first now
    if (item.ops) { setOps(item.ops); setAnnounce(`${item.label}: done. Not in the file until you save.`); return; }
    if (first === undefined || !rightMap) return;
    const el = rightMap.elements[first];
    if (item.act === "split-text") setSplitFor(first);
    else if (item.act === "delete") { if (menu && menu.ids.length > 1) setDeleteMany(menu.ids); else setDeleteFor(first); }
    else if (item.act === "read") { selectFrom("right")(first); setReading(true); setReadFrom((r) => ({ i: first, n: (r?.n ?? 0) + 1 })); }
    else if (item.act === "report" && el && rightVersion) navigate(reportLink(id, rightVersion.number, el, el.boxes[0]?.page ?? rightPage));
  };

  function openTree(side: "left" | "right") {
    setTree(side);
    setHover(null);
    // The tree replaces this pane's document, so a selection made there no longer has anywhere to show
    if (selected?.side === side) setSelected(null);
  }

  // --- one page at a time ---------------------------------------------------------------------
  const [pageCount, setPageCount] = useState(0);
  const noteLayout = (side: "left" | "right") => (l: PaneLayout) => {
    layouts.current[side] = l;
    const n = Math.max(layouts.current.left?.heights.length ?? 0, layouts.current.right?.heights.length ?? 0);
    setPageCount((c) => (c === n ? c : n));
  };
  const goToPage = useCallback((n: number) => {
    const next = Math.max(1, Math.min(pageCount || 1, Math.round(n) || 1));
    if (next !== onePage) setAnnounce(`Page ${next} of ${pageCount}.`);
    setOnePage(next);
  }, [pageCount, onePage]);
  // What's being typed in the page box (null: it shows the page). Enter or leaving the box turns to it; anything
  // that isn't a number puts the page back, Escape too
  const [pageText, setPageText] = useState<string | null>(null);
  function typedTo(text: string) {
    const n = typedPage(text, pageCount);
    if (n !== null) goToPage(n);
    setPageText(null);
  }
  function setView(v: "all" | "one", sure = false) {
    viewChosen.current = true;
    if (v === "all" && long && !sure) { setAskAll(true); return; }
    // Stay on the page being looked at, either way
    const page = mostVisible("right") ?? mostVisible("left") ?? 1;
    setViewState(v);
    if (v === "one") setOnePage(page);
    else window.setTimeout(() => { scrollTo("left", page); scrollTo("right", page); }, 80);
  }
  useEffect(() => { if (view === "one") setRightPage(onePage); }, [view, onePage]);
  useEffect(() => { if (pageCount && onePage > pageCount) setOnePage(pageCount); }, [pageCount, onePage]);
  // [ and ] turn the page in one-page view (not while typing in a field)
  useEffect(() => {
    if (view !== "one") return;
    const key = (e: KeyboardEvent) => {
      const t = e.target;
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      if (t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
      if (e.key === "[") goToPage(onePage - 1);
      else if (e.key === "]") goToPage(onePage + 1);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [view, onePage, goToPage]);
  const only = view === "one" ? onePage : null;

  const rightVersion = list.find((v) => v.number === right);
  // "Review every figure" is offered unless the document's AI tier has it switched off
  const choices = useLoad(() => get<ServiceChoice[]>("/services"), []);
  const tierChoice = job.data?.ai_tier ? choices.data?.find((c) => c.key === job.data!.ai_tier) : undefined;
  const allowFullReview = !job.data?.ai_tier || (tierChoice?.allow_full_review ?? true);
  const leftVersion = list.find((v) => v.number === left);
  const editable = !!versions.data?.can_edit && !!rightVersion && rightVersion.kind !== "original" && !readOnlyFor && !lostDraft
    && !(sent && waitingFor !== null);  // a save being checked: its changes are shown, and can't be changed now
  // What the panes draw: the unsaved changes, or while a save is checked, the ones it sent
  const shownOps = sent && waitingFor !== null ? sent : stored;
  // "Describe with AI" on a part of the page (a figure's piece, a drawn area): one call at the document's AI level
  // A document made without AI is never sent to one afterwards either (#659)
  const withAi = !!job.data?.ai_tier && !job.data?.no_ai;
  const describeArea = me?.can_describe && withAi && editable && rightVersion ? async (page: number, bbox: [number, number, number, number]) => {
    const r = await post<{ alt: string }>(`/jobs/${id}/versions/${rightVersion.number}/describe`, { page, bbox, long: true });
    return r.alt;
  } : undefined;
  // "Suggest tags with AI" for a drawn area: allowed and costed as Describe with AI is
  const suggestArea = describeArea && rightVersion ? (page: number, bbox: [number, number, number, number],
    items: { kind: string; text: string; bbox: [number, number, number, number] }[]) =>
    post<AreaTags>(`/jobs/${id}/versions/${rightVersion.number}/area-tags`, { page, bbox, items }) : undefined;
  // A tag just made (from an area, the AI's tags for one, a split) is chosen at once, like any tag (#692). While a new
  // tag is chosen, a bar above the pages changes its type and alt text in the edit that makes it, or takes it back
  const [barClosed, setBarClosed] = useState<number | null>(null);
  const lastStored = useRef(stored);
  useEffect(() => {
    const at = justMade(lastStored.current, stored);
    lastStored.current = stored;
    const m = at === null ? undefined : draft?.made.find((x) => x.op === at);
    if (!m) return;
    setSelected({ side: "right", i: m.i });
    setAlso(ALONE);
    setAnnounce("New tag chosen. Not in the file until you save; it can be changed like any tag.");
  }, [stored, draft]);
  const madeTags = useMemo(() => (draft && editable ? draft.made.filter((m) => makesTag(stored[m.op])).map((m) => {
    const el = draft.map.elements[m.i];
    return { ...m, role: el.role, alt: el.alt ?? "", text: el.text ?? "" };
  }) : []), [draft, editable, stored]);
  const choosePending = (i: number) => {
    setSelected({ side: "right", i });
    setAlso(ALONE);
    setBarClosed(null);
    const b = rightMap?.elements[i]?.boxes[0];
    if (b) scrollTo("right", b.page, b.bbox[1], b.bbox[3]);
  };
  const pendingTag = selected?.side === "right" && barClosed !== selected.i ? madeTags.find((t) => t.i === selected.i) : undefined;
  // Undo and redo, saying what was undone or redone: the edit one list has and the other doesn't
  const stepHistory = useCallback((way: "undo" | "redo") => {
    const next = way === "undo" ? undo(history) : redo(history);
    if (next === history) return;
    const [more, fewer] = next.present.length >= history.present.length ? [next.present, history.present] : [history.present, next.present];
    const op = more.find((o) => !fewer.includes(o)) ?? next.present.find((o, n) => o !== history.present[n]);
    // Described with the new tags it names as they are in the list it's in
    const list = more.includes(op!) ? more : next.present;
    const shown = op && rightData ? expand(rightData.tagmap, list) : null;
    const what = op && shown ? `: ${describe(shown.ops[list.indexOf(op)], shown.map)}` : "";
    setHistory(next);
    setAnnounce(`${way === "undo" ? "Undone" : "Redone"}${what}. ${next.present.length} unsaved change${next.present.length === 1 ? "" : "s"}.`);
  }, [history, rightData]);
  useEffect(() => {
    if (!editable) return;
    const key = (e: KeyboardEvent) => {
      const way = historyKey(e, e.target);
      if (!way || document.querySelector("dialog[open]")) return;  // a window open has its own fields and buttons
      e.preventDefault();
      stepHistory(way);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [editable, stepHistory]);
  const selectedEl = selected?.side === "right" && rightMap ? rightMap.elements[selected.i] ?? null : null;
  const selectedFindings = useMemo(
    () => (rightVersion?.summary?.findings ?? []).filter((f) => selectedEl && f.element === selectedEl.i),
    [rightVersion, selectedEl],
  );

  async function changeRight(n: number) {
    if (ops.length && !(await confirmLeave())) return;
    setIsolated(null); // its number means a tag of this version
    resetOps([]);
    setSelected(null);
    setArea(null);
    setAreaMode(false);
    setRight(n);
  }

  async function review(full: boolean) {
    if (!rightVersion) return;
    setReviewBusy(true);
    setSaveError(null);
    try {
      await post<DocVersion>(`/jobs/${id}/versions/${rightVersion.number}/review`, { full });
      setAnnounce(`AI review of ${rightVersion.label} started.`);
      await versions.reload();
    } catch (e) {
      setSaveError(e);
    } finally {
      setReviewBusy(false);
    }
  }

  async function save(note: string, aiReview: "none" | "sampled" | "full", replace = false) {
    if (!rightVersion) return;
    setSaving(true);
    setSaveError(null);
    try {
      const v = await post<DocVersion>(`/jobs/${id}/versions`, {
        based_on: rightVersion.number, edits: stored, note: note || null, review: aiReview, replace,
      });
      setSent(stored);
      resetOps([]);
      // The draft became this version (the server removed it)
      hasDraft.current = false;
      lastKept.current = "";
      setRestored(null);
      setKept({ at: null, dirty: false, error: false });
      setFailedSave(null);
      setRetried(null);
      setWaitingFor(v.number);
      setAnnounce(`Saving ${v.label}. The tags are being checked.`);
      await versions.reload();
    } catch (e) {
      setSaveError(e);
    } finally {
      setSaving(false);
    }
  }

  if (job.loading || versions.loading) return <Loading />;
  if (job.error || versions.error) return <ErrorNotice error={job.error ?? versions.error} />;
  const waiting = waitingFor !== null ? list.find((v) => v.number === waitingFor) : undefined;
  // The titles of the notes folded under the heading, shown while folded: one for each notice below it
  const notes = ([
    failed && !waiting && { title: `${failed.label} wasn't saved`, warn: true },
    retried && ops.length > 0 && { title: `The changes of ${retried.label} are back` },
    restored && ops.length > 0 && { title: restored.from ? `Started from ${restored.from}'s unsaved changes` : "Your unsaved changes are back" },
    lostDraft && { title: "Your earlier unsaved changes can't be applied", warn: true },
    readOnlyFor && { title: "Read-only" },
    canEditDoc && (kept.at || kept.error) && ops.length > 0 && (kept.error ? { title: "Changes not kept on the server", warn: true }
      : { title: `Unsaved changes kept ${kept.at!.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` }),
  ] as const).filter((n): n is { title: string; warn?: boolean } => !!n);

  // The viewing controls sit in the panes' own header rows, beside the version pickers: what's shown on the left,
  // how it's shown on the right
  const leftTools = (
    <div className="pv-tools">
          <label htmlFor="pv-view" className="small">View</label>
          <select id="pv-view" value={view} onChange={(e) => setView(e.target.value as "all" | "one")}>
            <option value="all">All pages</option>
            <option value="one">One page</option>
          </select>
          {view === "one" && (
            <div className="pv-pager" role="group" aria-label="Page">
              <button className="pv-pager-btn" disabled={onePage <= 1} onClick={() => goToPage(onePage - 1)}
                title="Previous page ( [ )"><Chevron up /><span className="visually-hidden">Previous page</span></button>
              <span className="pv-pager-divider" aria-hidden="true" />
              <button className="pv-pager-btn" disabled={onePage >= pageCount} onClick={() => goToPage(onePage + 1)}
                title="Next page ( ] )"><Chevron /><span className="visually-hidden">Next page</span></button>
              <label htmlFor="pv-page" className="visually-hidden">Page</label>
              <input id="pv-page" className="pv-page-input" type="text" inputMode="numeric" autoComplete="off"
                style={{ width: `calc(${String(pageCount || 1).length}ch + 22px)` }}
                value={pageText ?? String(onePage)} onChange={(e) => setPageText(e.target.value)}
                onFocus={(e) => e.target.select()} onBlur={(e) => typedTo(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") typedTo(e.currentTarget.value);
                  else if (e.key === "Escape") setPageText(null);
                }} />
              <span className="small muted">of {pageCount}</span>
            </div>
          )}
          <label htmlFor="pv-mode" className="small">Show</label>
          <select id="pv-mode" value={mode} onChange={(e) => setMode(e.target.value as OverlayMode)}>
            {MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
    </div>
  );
  // On the picker's own line, at its right-hand end: nothing here is worth a line of its own
  const rightTools = (
    <div className="pv-tools">
      <label className="check small" title="A tag's details on hover and focus. Off for the rest of this session until you turn them back on.">
        <input type="checkbox" checked={cards} onChange={(e) => setCards(e.target.checked)} /> Tag cards</label>
      <label htmlFor="pv-zoom" className="visually-hidden">Zoom</label>
      <select id="pv-zoom" value={zoom} onChange={(e) => setZoom(Number(e.target.value))}>
        {ZOOMS.map((z) => <option key={z} value={z}>{z === 1 ? "Fit width" : `${Math.round(z * 100)}%`}</option>)}
      </select>
      <label className="check small"><input type="checkbox" checked={sync} onChange={(e) => setSync(e.target.checked)} /> Scroll together</label>
    </div>
  );

  return (
    <MarksContext.Provider value={marks}>
      {askAll && (
        <Ask title={`Show all ${pages.toLocaleString()} pages at once?`} onCancel={() => setAskAll(false)}
          actions={[{ label: "Show all pages", run: () => { setAskAll(false); setView("all", true); } }]}
          cancel="Stay on one page">
          <p>This is a long document. Drawing every page takes a while, and scrolling may be slow. One page at a time
            stays quick; you can turn pages with the arrows or the [ and ] keys.</p>
        </Ask>
      )}
      {otherDraft && (
        <Ask title={`${otherDraft.user_email} has unsaved changes on this document`} onCancel={() => { setReadOnlyFor(otherDraft); setOtherDraft(null); }}
          actions={[
            { label: "Start my own", secondary: true, run: () => setOtherDraft(null) },
            { label: "Start from their changes", run: () => void startFrom(otherDraft) },
          ]}
          cancel="View read-only">
          <p>{otherDraft.edits_count.toLocaleString()} change{otherDraft.edits_count === 1 ? "" : "s"}, last kept {fmtDate(otherDraft.updated_at)}.
            Each person's unsaved changes are their own: starting yours, from scratch or from theirs, leaves theirs as they are.
            Two sets of changes saved separately make two versions, so it's worth agreeing who carries on.</p>
        </Ask>
      )}
      {askJump && (
        <Ask title={`This ${askJump.e.role} tag starts on page ${askJump.first}`} onCancel={() => setAskJump(null)}
          actions={[
            { label: `Go to page ${askJump.first}`, run: () => { const a = askJump; setAskJump(null); showFromTree(a.side)(a.e, true); } },
            ...(askJump.here ? [{ label: `Select it and stay on page ${onePage}`, secondary: true,
              run: () => { const a = askJump; setAskJump(null); setSelected({ side: a.side, i: a.e.i }); } }] : []),
          ]}
          cancel="Cancel">
          <p>It covers {askJump.pages.toLocaleString()} pages, from page {askJump.first} to page {askJump.last}.
            {askJump.here ? ` Part of it is on page ${onePage}, the one you're looking at.` : ` None of it is on page ${onePage}, the one you're looking at.`}</p>
        </Ask>
      )}
      <div className="pv-top" ref={setTopEl}>
        <p className="small pv-top-back"><Link to={backTo}>← Back to the document</Link></p>
        <div className="pv-top-title">
          <h1>Tag manager: {job.data?.filename}</h1>
          <p className="sub small">{cards ? "Hover over or focus an outlined tag for details." : "Tag cards are off. Right-click a tag for its details."}</p>
        </div>
        <div className="actions pv-top-actions">
          {selected?.side === "right" && rightMap && (
            // The menu for the selected tags, for the keyboard and for anyone who doesn't right-click
            <button className="btn secondary small" aria-haspopup="menu" onClick={(e) => {
              const box = e.currentTarget.getBoundingClientRect();
              setMenu({ x: box.left, y: box.bottom + 4, ids: chosen, at: null });
            }}>Actions{chosen.length > 1 ? ` (${chosen.length})` : ""}<span aria-hidden="true"> ▾</span></button>
          )}
          {editable && (history.past.length > 0 || history.future.length > 0) && (
            <span className="pv-history" role="group" aria-label="Undo and redo">
              <button className="btn secondary small" disabled={!history.past.length} onClick={() => stepHistory("undo")}
                aria-keyshortcuts="Control+Z Meta+Z" title="Undo the last change (Ctrl+Z, ⌘Z on a Mac)">Undo</button>
              <button className="btn secondary small" disabled={!history.future.length} onClick={() => stepHistory("redo")}
                aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z Control+Y" title="Redo (Ctrl+Shift+Z, ⇧⌘Z on a Mac)">Redo</button>
            </span>
          )}
          {editable && ops.length > 0 && rightVersion && (
            <button className="btn small" aria-haspopup="dialog" onClick={() => setHeadingDialog("changes")}>Unsaved changes ({ops.length})…</button>
          )}
          {rightMap && rightVersion && (
            <button className="btn secondary small" aria-haspopup="dialog" onClick={() => setHeadingDialog("document")}
              title="The document's title and language">Document…</button>
          )}
          {rightVersion && (rightVersion.kind === "reviewed" || rightVersion.kind === "corrected") && (
            <button className="btn secondary small" aria-haspopup="dialog" onClick={() => setHeadingDialog("review")}>
              AI review{rightVersion.review_status === "queued" || rightVersion.review_status === "running" ? " (running)" : ""}…
            </button>
          )}
          {isolated !== null && (
            <button className="btn small" onClick={() => { setIsolated(null); setAnnounce("Showing everything."); }}>Show everything</button>
          )}
          {usable.length > 0 && rightVersion && tree !== "right" && (
            <button className="btn secondary small" aria-pressed={panel} onClick={() => setPanel(!panel)}
              title={panel ? "Hide the editing panel" : "Show the editing panel beside the pages"}>Panel</button>
          )}
          {usable.length > 0 && (
            <button className="btn secondary small" aria-pressed={reading} onClick={() => { setReading(!reading); setReadFrom(null); }}>Read aloud</button>
          )}
          {job.data && me && job.data.user_id === me.id && me.can_submit && rightVersion && (
            <Link className="btn secondary small" to={reportLink(id, rightVersion.number, selectedEl ?? null, rightPage)}
              title={selectedEl ? "About the selected tag" : "About this document. Select a tag first to report a problem with that tag."}>
              {reportLabel(selectedEl ?? null)}
            </Link>
          )}
        </div>
        {/* In the heading, which stays in view: the reader and the tag being read are seen together */}
        {reading && usable.length > 0 && (
          <Reader jobId={id} ops={shownOps} selected={selected} onShow={showRead} onClose={() => { setReading(false); setReadFrom(null); }}
            from={readFrom}
            versions={{
              left: leftVersion && left !== null ? { number: left, label: leftVersion.label } : null,
              right: rightVersion && right !== null ? { number: right, label: rightVersion.label } : null,
            }} />
        )}
      </div>
      <LiveRegion message={announce} />
      <LiveRegion message={marks.said} />
      {usable.length === 0 ? (
        <Notice kind="info">The tag manager isn't available for this document. Documents processed before it was added need to be submitted again.</Notice>
      ) : (
        <>
          {areaMode && editable && <Notice kind="info">Drag on the right-hand page to draw an area. What's inside it opens in a window. Escape stops.</Notice>}
          {waiting && <Notice kind="info" title={`Saving ${waiting.label}`}>The tags are being checked and validated. Meanwhile the tree and the pages show the changes it's making, and
            can't be changed; this view switches to it when it's ready.</Notice>}
          <ErrorNotice error={saveError} />
          <ErrorNotice error={marks.error} />
          {/* Notes about the document and the unsaved changes, folded under the heading so the pages keep the room;
              their titles show while folded */}
          {notes.length > 0 && (
            <details className="pv-notes" open={notesOpen} onToggle={(e) => setNotesOpen(e.currentTarget.open)}>
              <summary>
                <span className="pv-notes-count">{notes.length === 1 ? "1 note" : `${notes.length} notes`}</span>
                {notes.map((n, k) => <span key={k} className={n.warn ? "pv-note-warn" : undefined}>{n.title}</span>)}
              </summary>
            {failed && !waiting && (
              <Notice kind="warn" title={`${failed.label} wasn't saved`}>
                {failed.error_message}
                {canEditDoc && (
                  <>{" "}
                    {ops.length ? (
                      <span className="muted">To try its changes again, save your unsaved changes or{" "}
                        <button className="linkbtn" onClick={() => {
                          setOps([]);
                          setAnnounce("Your unsaved changes were discarded. Undo brings them back.");
                        }}>discard them</button> (Undo brings them back).</span>
                    ) : <button className="linkbtn" onClick={() => void tryAgain(failed)}>Try its changes again</button>}{" "}
                    <button className="linkbtn" onClick={() => { setNotAgain(failed.number); setFailedSave(null); }}>Dismiss</button>
                  </>
                )}
              </Notice>
            )}
            {retried && ops.length > 0 && (
              <Notice kind="info" title={`The changes of ${retried.label} are back`}>
                {retried.count.toLocaleString()} change{retried.count === 1 ? "" : "s"}, as unsaved changes on the version they were
                made on. Save them again; if one is refused again, take it out under Unsaved changes first.
              </Notice>
            )}
            {restored && ops.length > 0 && (
              <Notice kind="info" title={restored.from ? `Started from ${restored.from}'s unsaved changes` : "Your unsaved changes are back"}>
                {restored.count.toLocaleString()} change{restored.count === 1 ? "" : "s"}, last kept {fmtDate(restored.at)}
                {restored.from ? ". Theirs are untouched; these are now yours." : ". They're listed under Unsaved changes, where you can undo any of them or discard them all."}
              </Notice>
            )}
            {lostDraft && (
              <Notice kind="warn" title="Your earlier unsaved changes can't be applied">
                They were made on a version of this document that is no longer available ({lostDraft.edits.length.toLocaleString()} changes,
                last kept {fmtDate(lostDraft.updated_at)}).{" "}
                <button className="linkbtn" onClick={() => { void put(`/jobs/${id}/tag-draft`, { based_on: lostDraft.based_on, edits: [] }).then(() => { hasDraft.current = false; setLostDraft(null); }).catch(setSaveError); }}>
                  Discard them and start again</button>
              </Notice>
            )}
            {readOnlyFor && (
              <Notice kind="info" title="Read-only">
                {readOnlyFor.user_email} has unsaved changes on this document, last kept {fmtDate(readOnlyFor.updated_at)}.{" "}
                <button className="linkbtn" onClick={() => setReadOnlyFor(null)}>Start my own changes</button>
              </Notice>
            )}
            {canEditDoc && (kept.at || kept.error) && ops.length > 0 && (
              <p className="muted small" role="status">
                {kept.error ? "Your changes couldn't be kept on the server just now; they're still here in this tab. Trying again with your next change."
                  : `Unsaved changes kept ${kept.at!.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}. You can close this page and carry on later; nothing is in the file until you save.`}
              </p>
            )}
            </details>
          )}
          {pendingTag && stored[pendingTag.op] && (
            <PendingTag tag={pendingTag} op={stored[pendingTag.op]} all={madeTags} onChoose={choosePending}
              onClose={() => {
                setBarClosed(pendingTag.i);
                setAnnounce("New tag bar closed. The tag stays chosen.");
                scrollEls.current.right?.focus({ preventScroll: true }); // the bar had focus: back to the pages
              }}
              onChange={(role, alt) => {
                const next = changePending(stored, pendingTag.op, role, alt, undefined, pendingTag.group);
                if (!next) return false;
                setStored(next);
                setAnnounce(`The new tag is now ${ROLE_NAMES[role] ?? role}. Not in the file until you save.`);
                return true;
              }}
              onRemove={() => {
                const back = takeBack(stored, pendingTag.op, pendingTag.group);
                setStored(back.ops);
                setSelected(null);
                setAnnounce(`The new tag was taken back${back.dropped ? `, with ${back.dropped} change${back.dropped === 1 ? "" : "s"} made to it. Undo brings them back` : ""}.`);
                scrollEls.current.right?.focus({ preventScroll: true });
              }} />
          )}
          <div className={`pv-grid${tree === "right" || !panel ? " no-side" : ""}`} ref={gridRef}
            style={{ "--pv-l": `${split}fr`, "--pv-r": `${100 - split}fr`, "--pv-side": `${sideWidth}px` } as React.CSSProperties}>
            {left !== null && (tree === "left" && right !== null && rightVersion ? (
              <TreePane side="left" tools={leftTools} jobId={id} number={right} sourceLabel={rightVersion.label}
                picker={<VersionPicker side="left" versions={list} value={TREE} treeDisabled={false}
                  onTree={() => {}} onNumber={(n) => { setLeft(n); setTree(null); setHover(null); }} />}
                findings={rightVersion.summary?.findings ?? []} ops={shownOps} follow={follow} page={only}
                onUndoMove={editable ? (el) => { setOps(undoMoves(ops, el.i)); } : undefined}
                onMove={editable ? (el, target, position) => setOps([...ops, { op: "move", element: el.i, expect_role: el.role,
                  target: target.i, expect_target_role: target.role, position }]) : undefined}
                selected={selected?.side === "right" ? selected.i : null} onSelect={showFromTree("right")}
                picked={picked} onPick={(el, how, visible) => pickRight(el.i, how, visible)} isolated={isolated}
                onMenu={(el, x, y) => openMenu(el.i, x, y)}
                onReadAll={(el) => { selectFrom("right")(el.i); setEditFor(el.i); }}
                onHover={(i) => setHover(i === null ? null : { side: "right", i })} />
            ) : (
              <Pane side="left" tools={leftTools} jobId={id} versions={list} number={left} onNumber={setLeft} mode={mode} zoom={zoom} cards={cards}
                selected={selected?.side === "left" ? selected.i : null} onSelect={selectFrom("left")} ops={[]}
                scrollRef={(el) => { scrollEls.current.left = el; }} onScroll={onScroll("left")}
                onLayout={noteLayout("left")} onlyPage={only}
                highlight={hover?.side === "left" ? hover.i : null}
                onTree={() => openTree("left")} treeDisabled={tree === "right"} />
            ))}
            {left !== null && right !== null && (
              <Divider label="Divider between the two panes" value={split} min={SPLIT_MIN} max={SPLIT_MAX} text={`Left pane ${split}%`}
                at={(x) => { const panes = gridRef.current!.children; return splitAt(x, panes[0].getBoundingClientRect().left, panes[2].getBoundingClientRect().right); }}
                keyTo={splitKey} reset={SPLIT_DEFAULT} onChange={setSplit} onDone={(v) => saveSplit(localStore(), v)} />
            )}
            {right !== null && (tree === "right" && left !== null && leftVersion ? (
              <TreePane side="right" tools={rightTools} jobId={id} number={left} sourceLabel={leftVersion.label}
                picker={<VersionPicker side="right" versions={list} value={TREE} treeDisabled={false}
                  onTree={() => {}} onNumber={(n) => { if (n !== right) changeRight(n); setTree(null); setHover(null); }} />}
                findings={leftVersion.summary?.findings ?? []} ops={[]} follow={follow} page={only}
                selected={selected?.side === "left" ? selected.i : null} onSelect={showFromTree("left")}
                onHover={(i) => setHover(i === null ? null : { side: "left", i })} />
            ) : (
              <Pane side="right" tools={rightTools} jobId={id} versions={list} number={right} onNumber={changeRight} mode={mode} zoom={zoom} cards={cards}
                selected={selected?.side === "right" ? selected.i : null} onSelect={selectFrom("right")} ops={shownOps}
                scrollRef={(el) => { scrollEls.current.right = el; }} onScroll={onScroll("right")} onPage={setRightPage}
                onLayout={noteLayout("right")} onData={setRightData} onlyPage={only}
                areaMode={areaMode && editable} area={editable ? area : null} areaBoxes={area ? areaBoxes : null}
                onArea={(p, bbox) => { setArea({ page: p, bbox }); setRightPage(p); setAreaMode(false); setAreaOpen(true); }}
                highlight={hover?.side === "right" ? hover.i : null}
                picked={picked} onPick={(i) => pickRight(i, "toggle")} onMenu={openMenu}
                onPageMenu={(page, x, y) => setMenu({ x, y, ids: [], at: null, page })} isolated={isolated}
                spot={spot} onSpotClose={closeSpot}
                onSpot={(f) => { const s = spotFor(f, rightMap); if (s) showSpot(s); }}
                onTree={() => openTree("right")} treeDisabled={tree === "left"} />
            ))}
            {rightMap && rightVersion && tree !== "right" && panel && (
              <Divider className="pv-split-side" label="Divider before the editing panel" value={sideWidth} min={SIDE_MIN} max={SIDE_MAX}
                text={`Editing panel ${sideWidth} pixels wide`}
                at={(x) => sideAt(x, gridRef.current!.getBoundingClientRect().right)}
                keyTo={sideKey} reset={SIDE_DEFAULT} onChange={setSideWidth} onDone={(v) => saveSide(localStore(), v)} />
            )}
            {rightMap && rightVersion && tree !== "right" && panel && (
              <EditPanel map={rightMap} element={selectedEl} findings={selectedFindings} page={rightPage}
                ops={ops} setOps={setOps} onSelect={(i) => selectFrom("right")(i)} canEdit={editable}
                readOnlyReason={!versions.data?.can_edit
                  ? "You can view this document's tags. Only its owner and their group admins can edit them."
                  : readOnlyFor ? `You're viewing this document read-only while ${readOnlyFor.user_email} has unsaved changes.`
                  : lostDraft ? "Your earlier unsaved changes can't be applied (see the note above)."
                  : "The original can't be edited. Choose a remediated or reviewed version on the right to edit it."}
                baseLabel={rightVersion.label} saving={saving} onSave={(n, r, into) => void save(n, r, into)}
                version={rightVersion} onReview={withAi ? (full) => void review(full) : undefined} reviewBusy={reviewBusy} jump={jump} describe={describeArea} suggest={suggestArea} onBoxes={setAreaBoxes}
                allowFullReview={allowFullReview} area={area} setArea={setArea} areaMode={areaMode} setAreaMode={setAreaMode}
                count={chosen.length} onActions={selectedEl ? (from) => {
                  const box = from.getBoundingClientRect();
                  setMenu({ x: box.left, y: box.bottom + 4, ids: chosen, at: null });
                } : undefined} />
            )}
          </div>
          {menu && rightMap && (
            <TagMenu x={menu.x} y={menu.y} title={menu.page ? `Page ${menu.page}` : menu.ids.length > 1 ? `${menu.ids.length} tags selected` : "This tag"}
              items={menu.page ? [...pageMenu(menu.page, area !== null, areaMode, editable), ...(isolated !== null ? [isolateItem(isolated, null)] : [])]
                : [...menuAndTypes(tagMenu(rightMap, ops, menu.ids, menu.at, editable)).menu.filter((it) => it.act !== "report" || canReport),
                  isolateItem(isolated, menu.ids[0] ?? null)]}
              onChoose={choose} onClose={() => setMenu(null)} />
          )}
          {tableFor && rightMap && (
            <TableDialog map={rightMap} ops={ops} table={tableFor.table} tags={tableFor.tags} onClose={() => setTableFor(null)}
              onApply={(op) => { setOps(addEdit(ops, op)); setTableFor(null); setAlso(ALONE); setAnnounce(`${tableFor.table !== undefined ? "Table edited" : "Table made"}. Not in the file until you save.`); }} />
          )}
          {buildFrom && rightMap && (
            <ListBuilder els={inReadingOrder(rightMap, buildFrom)} onClose={() => setBuildFrom(null)}
              onApply={(op) => { setOps([...ops, op]); setAlso(ALONE); setAnnounce("List built. Not in the file until you save."); }} />
          )}
          {editFor !== null && rightMap?.elements[editFor] && rightVersion && (
            <TagDialog map={rightMap} element={rightMap.elements[editFor]} ops={ops} setOps={setOps} canEdit={editable}
              findings={(rightVersion.summary?.findings ?? []).filter((f) => f.element === editFor)}
              onSelect={(i) => selectFrom("right")(i)} onClose={() => setEditFor(null)} />
          )}
          {figureFor && rightData && rightMap?.elements[figureFor.i] && (
            <FigureSplit map={rightMap} element={rightMap.elements[figureFor.i]} pdf={rightData.pdf} preselect={figureFor.keys}
              describe={describeArea}
              onClose={() => setFigureFor(null)}
              onApply={(edits) => { setOps([...ops, ...edits]); setAnnounce("Figure split. Not in the file until you save."); }} />
          )}
          {deleteFor !== null && rightMap?.elements[deleteFor] && (
            <DeleteTag map={rightMap} element={rightMap.elements[deleteFor]} ops={ops} onCancel={() => setDeleteFor(null)}
              onDelete={(children) => {
                const el = rightMap.elements[deleteFor];
                setDeleteFor(null);
                setOps(addEdit(ops, { op: "delete", element: el.i, expect_role: el.role, children }));
                const up = draftTree(rightMap, ops).parent.get(el.i);
                if (up !== null && up !== undefined) selectFrom("right")(up);
                setAnnounce(`${children === "lift" ? "Tag removed" : "Tag deleted"}. Not in the file until you save.`);
              }} />
          )}
          {deleteMany && rightMap && (
            <DeleteTags map={rightMap} ids={inReadingOrder(rightMap, deleteMany).map((e) => e.i)} ops={ops}
              onCancel={() => setDeleteMany(null)}
              onDelete={(next, done, children) => {
                setDeleteMany(null);
                setAlso(ALONE);
                setOps(next);
                const up = draftTree(rightMap, ops).parent.get(done[0]);
                if (up !== null && up !== undefined) selectFrom("right")(up);
                setAnnounce(`${done.length} tags ${children === "lift" ? "removed" : "deleted"}. Not in the file until you save.`);
              }} />
          )}
          {headingDialog === "changes" && editable && ops.length > 0 && rightMap && rightVersion && (
            <HeadingDialog title="Unsaved changes" className="pv-savedlg" onClose={() => setHeadingDialog(null)}>
              <SaveChanges map={rightMap} ops={ops} setOps={setOps} version={rightVersion} baseLabel={rightVersion.label} saving={saving}
                allowFullReview={allowFullReview} prefix="savedlg" aiReview={withAi}
                onSave={(n, r, into) => { setHeadingDialog(null); void save(n, r, into); }} />
            </HeadingDialog>
          )}
          {headingDialog === "document" && rightMap && (
            <HeadingDialog title="Document" className="pv-docdlg" onClose={() => setHeadingDialog(null)}>
              <DocumentPanel map={rightMap} ops={ops} setOps={setOps} canEdit={editable}
                onDone={() => { setHeadingDialog(null); setAnnounce("Added to the unsaved changes."); }} />
            </HeadingDialog>
          )}
          {headingDialog === "review" && rightVersion && (
            <HeadingDialog title="AI review" className="pv-reviewdlg" onClose={() => setHeadingDialog(null)}>
              <h2>AI review of {rightVersion.label}</h2>
              <ReviewStatus version={rightVersion} canEdit={editable} busy={reviewBusy} allowFull={allowFullReview}
                onReview={withAi ? (full) => { setHeadingDialog(null); void review(full); } : undefined} />
            </HeadingDialog>
          )}
          {areaOpen && area && !areaMode && rightMap && editable && (
            <AreaDialog map={rightMap} page={area.page} element={selectedEl} ops={ops} setOps={setOps} area={area} setArea={setArea}
              areaMode={areaMode} setAreaMode={(on) => { setAreaMode(on); if (on) setAreaOpen(false); }}
              onSelect={(i) => selectFrom("right")(i)} onClose={() => { setAreaOpen(false); setArea(null); }} describe={describeArea} suggest={suggestArea} onBoxes={setAreaBoxes}
              onFigure={(el, keys) => { setAreaOpen(false); setArea(null); setFigureFor({ i: el, keys }); }} />
          )}
          {splitFor !== null && rightMap?.elements[splitFor] && (
            <SplitDialog element={rightMap.elements[splitFor]} onClose={() => setSplitFor(null)}
              onSplit={(at, char) => {
                const el = rightMap.elements[splitFor];
                setOps([...ops, { op: "split", element: el.i, expect_role: el.role, at, char }]);
                setAnnounce("Split: done. Not in the file until you save.");
              }} />
          )}
          {typesFor && rightMap && (
            <TypeDialog title={typesFor.length > 1 ? `${typesFor.length} tags selected` : "The selected tag"}
              types={menuAndTypes(tagMenu(rightMap, ops, typesFor, null, editable)).types}
              onChoose={(item) => { if (item.ops) { setOps(item.ops); setAnnounce(`Changed to ${item.label}. Not in the file until you save.`); } }}
              onClose={() => setTypesFor(null)} />
          )}
        </>
      )}
    </MarksContext.Provider>
  );
}

/** A bar between two parts of the viewer that is dragged, or moved with the arrow keys, to give one more room.
 * Enter or a double-click puts it back where it starts. */
function Divider({ label, value, min, max, text, at, keyTo, reset, onChange, onDone, className = "" }: {
  label: string; value: number; min: number; max: number; text: string; className?: string; reset: number;
  at: (clientX: number) => number; keyTo: (key: string, now: number) => number | null;
  onChange: (v: number) => void; onDone: (v: number) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const last = useRef(value);
  last.current = value;
  return (
    <div className={`pv-split ${className}${dragging ? " dragging" : ""}`} role="separator" aria-orientation="vertical" tabIndex={0}
      aria-label={label} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} aria-valuetext={text}
      title="Drag to resize; double-click to reset"
      onPointerDown={(e) => {
        // preventDefault keeps the drag from selecting text, and would keep the focus away too
        e.currentTarget.setPointerCapture(e.pointerId); e.currentTarget.focus(); setDragging(true); e.preventDefault();
      }}
      onPointerMove={(e) => { if (dragging) onChange(at(e.clientX)); }}
      onPointerUp={() => { setDragging(false); onDone(last.current); }}
      onPointerCancel={() => setDragging(false)}
      onDoubleClick={() => { onChange(reset); onDone(reset); }}
      onKeyDown={(e) => {
        const next = keyTo(e.key, value);
        if (next === null) return;
        e.preventDefault();
        onChange(next);
        onDone(next);
      }} />
  );
}

function localStore(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

/** A question before doing something the person may not expect. Escape and the cancel button both leave things
 * as they are; focus starts on cancel. */
function Ask({ title, children, actions, cancel, onCancel }: {
  title: string; children: React.ReactNode; cancel: string; onCancel: () => void;
  actions: { label: string; run: () => void; secondary?: boolean }[];
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => { if (ref.current && !ref.current.open) ref.current.showModal(); }, []);
  return (
    <dialog ref={ref} className="confirm-dialog" aria-labelledby={`${id}-h`} onCancel={(e) => { e.preventDefault(); onCancel(); }}>
      <h2 id={`${id}-h`}>{title}</h2>
      {children}
      <div className="actions">
        <button className="btn secondary" type="button" autoFocus onClick={onCancel}>{cancel}</button>
        {actions.map((a) => (
          <button key={a.label} className={`btn${a.secondary ? " secondary" : ""}`} type="button" onClick={a.run}>{a.label}</button>
        ))}
      </div>
    </dialog>
  );
}

/** The page buttons' arrows, as a PDF viewer draws them: up for the previous page, down for the next. */
function Chevron({ up = false }: { up?: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true"><path d={up ? "M6 15l6-6 6 6" : "M6 9l6 6 6-6"} /></svg>
  );
}
