import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import type { BackState } from "../pages/JobDetail";
import { get, post, type DocVersion, type Job, type ServiceChoice, type TagEdit, type TagElement, type Versions } from "../api";
import { useAuth } from "../auth";
import { ErrorNotice, LiveRegion, Loading, Notice, useLoad, useTitle } from "../components/ui";
import { useConfirmLeave, useUnsavedChanges } from "../components/unsaved";
import { EditPanel } from "./EditPanel";
import { Pane, TREE, VersionPicker, type OverlayMode, type PaneLayout } from "./Pane";
import { TreePane } from "./TreePane";
import { clearVersionCache, type PaneData } from "./pdf";

// 1 = fit the page to the pane's width; larger values scroll sideways
const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3];

const MODES: { value: OverlayMode; label: string }[] = [
  { value: "flagged", label: "Flagged tags" },
  { value: "all", label: "All tags" },
  { value: "order", label: "Reading order" },
  { value: "off", label: "No overlay" },
];

function pending(v: DocVersion) {
  return v.status === "queued" || v.status === "submitted" || v.status === "processing"
    || v.review_status === "queued" || v.review_status === "running";
}

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
  useTitle(job.data ? `Tags: ${job.data.filename}` : "Tags");

  const list = versions.data?.versions ?? [];
  const usable = list.filter((v) => v.status === "ready" && v.has_tagmap);
  const [left, setLeft] = useState<number | null>(null);
  const [right, setRight] = useState<number | null>(null);
  const [mode, setMode] = useState<OverlayMode>("flagged");
  const [sync, setSync] = useState(true);
  const [zoom, setZoomState] = useState<number>(() => {
    try { return Number(localStorage.getItem("pv-zoom")) || 1; } catch { return 1; }
  });
  const [selected, setSelected] = useState<{ side: "left" | "right"; i: number } | null>(null);
  const [ops, setOps] = useState<TagEdit[]>([]);
  const [rightData, setRightData] = useState<PaneData | null>(null);
  const [rightPage, setRightPage] = useState(1);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [waitingFor, setWaitingFor] = useState<number | null>(null);
  const [announce, setAnnounce] = useState("");
  const [failedSave, setFailedSave] = useState<DocVersion | null>(null);
  // Either pane can show the tag tree of the other pane's version instead of a document
  const [tree, setTree] = useState<"left" | "right" | null>(null);
  const [hover, setHover] = useState<{ side: "left" | "right"; i: number } | null>(null);
  // Where the document next to the tree is scrolled to (page, y in page units), so the tree follows it
  const [follow, setFollow] = useState<{ page: number; y: number } | null>(null);
  const [jump, setJump] = useState(0);
  const [reviewBusy, setReviewBusy] = useState(false);
  const followFrame = useRef(0);

  // Default panes: original on the left; the newest reviewed version (or the remediated file) on the right.
  useEffect(() => {
    if (!usable.length) return;
    if (left === null) setLeft(Number(params.get("left") ?? usable[0].number));
    if (right === null) {
      const wanted = params.get("v") ?? params.get("right");
      const fallback = [...usable].reverse().find((v) => v.kind === "reviewed" || v.kind === "corrected") ?? usable.find((v) => v.kind === "primary") ?? usable[usable.length - 1];
      setRight(wanted !== null && usable.some((v) => v.number === Number(wanted)) ? Number(wanted) : fallback.number);
    }
  }, [usable, left, right, params]);

  useEffect(() => () => clearVersionCache(), []);

  // Ask before leaving with unsaved edits (links in the app as well as reload and close)
  useUnsavedChanges(ops.length > 0);
  const confirmLeave = useConfirmLeave();

  // When a save finishes, switch the right pane to the new version
  useEffect(() => {
    if (waitingFor === null) return;
    const v = list.find((x) => x.number === waitingFor);
    if (!v) return;
    if (v.status === "ready") {
      setAnnounce(`${v.label} is ready.`);
      setRight(v.number);
      setSelected(null);
      setWaitingFor(null);
    } else if (v.status === "failed") {
      setAnnounce(`Saving ${v.label} failed.`);
      setFailedSave(v);
      setWaitingFor(null);
    }
  }, [list, waitingFor]);

  // --- scroll sync ---------------------------------------------------------------------------
  const scrollEls = useRef<Record<string, HTMLDivElement | null>>({});
  const layouts = useRef<Record<string, PaneLayout>>({});
  // Where we last scrolled each pane to ourselves, so its echo scroll event isn't synced back
  const expected = useRef<Record<string, number | null>>({});

  const position = (side: string) => {
    const el = scrollEls.current[side];
    const l = layouts.current[side];
    if (!el || !l || !l.offsets.length) return null;
    const y = el.scrollTop;
    let page = 0;
    while (page + 1 < l.offsets.length && l.offsets[page + 1] <= y) page++;
    return { page, frac: Math.min(1, Math.max(0, (y - l.offsets[page]) / l.heights[page])) };
  };

  const onScroll = (side: "left" | "right") => () => {
    const self = scrollEls.current[side];
    const pos = position(side);
    if (side === "right" && pos) setRightPage(pos.page + 1);
    if (pos && tree && tree !== side) {
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
    const target = Math.round(l.offsets[page] + pos.frac * l.heights[page]);
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
        const target = Math.round(l.offsets[pos.page] + pos.frac * l.heights[pos.page]);
        expected.current[side] = target;
        el.scrollTop = target;
      }
    }, 50);
  }

  const scrollTo = useCallback((side: "left" | "right", page: number, y = 0) => {
    const el = scrollEls.current[side];
    const l = layouts.current[side];
    if (!el || !l || !l.offsets[page - 1]) return;
    el.scrollTop = l.offsets[page - 1] + y * l.scale - 60;
  }, []);

  // Jump to ?el= (from "Show in document" on the review list) once the right pane has loaded
  const jumped = useRef(false);
  useEffect(() => {
    const el = params.get("el");
    if (jumped.current || el === null || !rightData) return;
    const e = rightData.tagmap.elements[Number(el)];
    if (!e?.boxes.length) return;
    jumped.current = true;
    setSelected({ side: "right", i: e.i });
    window.setTimeout(() => scrollTo("right", e.boxes[0].page, e.boxes[0].bbox[1]), 100);
  }, [params, rightData, scrollTo]);

  const selectFrom = (side: "left" | "right") => (i: number | null) => {
    setSelected(i === null ? null : { side, i });
    if (i !== null && side === "right" && rightData) {
      const e = rightData.tagmap.elements[i];
      if (e?.boxes.length) setRightPage(e.boxes[0].page);
    }
  };

  // Clicking a tag in the tree selects it in the pane showing that version and scrolls to it
  const showFromTree = (docSide: "left" | "right") => (e: TagElement) => {
    selectFrom(docSide)(e.i);
    if (docSide === "right") setJump((n) => n + 1);
    if (e.boxes.length) scrollTo(docSide, e.boxes[0].page, e.boxes[0].bbox[1]);
  };

  function openTree(side: "left" | "right") {
    setTree(side);
    setHover(null);
    // The tree replaces this pane's document, so a selection made there no longer has anywhere to show
    if (selected?.side === side) setSelected(null);
  }

  const rightVersion = list.find((v) => v.number === right);
  // "Review every figure" is offered unless the document's AI tier has it switched off
  const choices = useLoad(() => get<ServiceChoice[]>("/services"), []);
  const tierChoice = job.data?.ai_tier ? choices.data?.find((c) => c.key === job.data!.ai_tier) : undefined;
  const allowFullReview = !job.data?.ai_tier || (tierChoice?.allow_full_review ?? true);
  const leftVersion = list.find((v) => v.number === left);
  const editable = !!versions.data?.can_edit && !!rightVersion && rightVersion.kind !== "original";
  const selectedEl = selected?.side === "right" && rightData ? rightData.tagmap.elements[selected.i] ?? null : null;
  const selectedFindings = useMemo(
    () => (rightVersion?.summary?.findings ?? []).filter((f) => selectedEl && f.element === selectedEl.i),
    [rightVersion, selectedEl],
  );

  async function changeRight(n: number) {
    if (ops.length && !(await confirmLeave())) return;
    setOps([]);
    setSelected(null);
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

  async function save(note: string, aiReview: "none" | "sampled" | "full") {
    if (!rightVersion) return;
    setSaving(true);
    setSaveError(null);
    try {
      const v = await post<DocVersion>(`/jobs/${id}/versions`, {
        based_on: rightVersion.number, edits: ops, note: note || null, review: aiReview,
      });
      setOps([]);
      setFailedSave(null);
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

  return (
    <>
      <div className="page-head">
        <div>
          <p className="small"><Link to={backTo}>← Back to the document</Link></p>
          <h1>Tags: {job.data?.filename}</h1>
          <p className="sub">Compare versions side by side. Hover over or focus an outlined tag for details.</p>
        </div>
        <div className="actions">
          {selectedEl && job.data && me && job.data.user_id === me.id && me.can_submit && rightVersion && (
            <Link className="btn secondary small" to={`/jobs/${id}?help=1&version=${rightVersion.number}${selectedEl.page ? `&page=${selectedEl.page}` : ""}&tag=${
              encodeURIComponent(JSON.stringify({ i: selectedEl.i, role: selectedEl.role, text: (selectedEl.alt ?? selectedEl.text ?? "").slice(0, 120) }))}`}>
              Report a problem here
            </Link>
          )}
          <label htmlFor="pv-mode" className="small">Show</label>
          <select id="pv-mode" value={mode} onChange={(e) => setMode(e.target.value as OverlayMode)}>
            {MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
          <div className="pv-zoom" role="group" aria-label="Zoom">
            <button className="btn secondary small" aria-label="Zoom out" disabled={zoom <= ZOOMS[0]}
              onClick={() => setZoom(ZOOMS[Math.max(0, ZOOMS.findIndex((z) => z >= zoom) - 1)])}>−</button>
            <label htmlFor="pv-zoom" className="visually-hidden">Zoom</label>
            <select id="pv-zoom" value={zoom} onChange={(e) => setZoom(Number(e.target.value))}>
              {ZOOMS.map((z) => <option key={z} value={z}>{z === 1 ? "Fit width" : `${Math.round(z * 100)}%`}</option>)}
            </select>
            <button className="btn secondary small" aria-label="Zoom in" disabled={zoom >= ZOOMS[ZOOMS.length - 1]}
              onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.findIndex((z) => z > zoom))])}>+</button>
          </div>
          <label className="check small"><input type="checkbox" checked={sync} onChange={(e) => setSync(e.target.checked)} /> Scroll together</label>
        </div>
      </div>
      <LiveRegion message={announce} />
      {usable.length === 0 ? (
        <Notice kind="info">The tag view isn't available for this document. Documents processed before the tag viewer was added need to be submitted again.</Notice>
      ) : (
        <>
          {waiting && <Notice kind="info" title={`Saving ${waiting.label}`}>The tags are being checked and validated. This view switches to it when it's ready.</Notice>}
          {failedSave && !waiting && (
            <Notice kind="warn" title={`${failedSave.label} wasn't saved`}>{failedSave.error_message}</Notice>
          )}
          <ErrorNotice error={saveError} />
          <div className={`pv-grid${tree === "right" ? " no-side" : ""}`}>
            {left !== null && (tree === "left" && right !== null && rightVersion ? (
              <TreePane side="left" jobId={id} number={right} sourceLabel={rightVersion.label}
                picker={<VersionPicker side="left" versions={list} value={TREE} treeDisabled={false}
                  onTree={() => {}} onNumber={(n) => { setLeft(n); setTree(null); setHover(null); }} />}
                findings={rightVersion.summary?.findings ?? []} ops={ops} follow={follow}
                selected={selected?.side === "right" ? selected.i : null} onSelect={showFromTree("right")}
                onHover={(i) => setHover(i === null ? null : { side: "right", i })} />
            ) : (
              <Pane side="left" jobId={id} versions={list} number={left} onNumber={setLeft} mode={mode} zoom={zoom}
                selected={selected?.side === "left" ? selected.i : null} onSelect={selectFrom("left")} ops={[]}
                scrollRef={(el) => { scrollEls.current.left = el; }} onScroll={onScroll("left")}
                onLayout={(l) => { layouts.current.left = l; }}
                highlight={hover?.side === "left" ? hover.i : null}
                onTree={() => openTree("left")} treeDisabled={tree === "right"} />
            ))}
            {right !== null && (tree === "right" && left !== null && leftVersion ? (
              <TreePane side="right" jobId={id} number={left} sourceLabel={leftVersion.label}
                picker={<VersionPicker side="right" versions={list} value={TREE} treeDisabled={false}
                  onTree={() => {}} onNumber={(n) => { if (n !== right) changeRight(n); setTree(null); setHover(null); }} />}
                findings={leftVersion.summary?.findings ?? []} ops={[]} follow={follow}
                selected={selected?.side === "left" ? selected.i : null} onSelect={showFromTree("left")}
                onHover={(i) => setHover(i === null ? null : { side: "left", i })} />
            ) : (
              <Pane side="right" jobId={id} versions={list} number={right} onNumber={changeRight} mode={mode} zoom={zoom}
                selected={selected?.side === "right" ? selected.i : null} onSelect={selectFrom("right")} ops={ops}
                scrollRef={(el) => { scrollEls.current.right = el; }} onScroll={onScroll("right")}
                onLayout={(l) => { layouts.current.right = l; }} onData={setRightData}
                highlight={hover?.side === "right" ? hover.i : null}
                onTree={() => openTree("right")} treeDisabled={tree === "left"} />
            ))}
            {rightData && rightVersion && tree !== "right" && (
              <EditPanel map={rightData.tagmap} element={selectedEl} findings={selectedFindings} page={rightPage}
                ops={ops} setOps={setOps} onSelect={(i) => selectFrom("right")(i)} canEdit={editable}
                readOnlyReason={!versions.data?.can_edit
                  ? "You can view this document's tags. Only its owner and their group admins can edit them."
                  : "The original can't be edited. Choose a remediated or reviewed version on the right to edit it."}
                baseLabel={rightVersion.label} saving={saving} onSave={(n, r) => void save(n, r)}
                version={rightVersion} onReview={(full) => void review(full)} reviewBusy={reviewBusy} jump={jump}
                allowFullReview={allowFullReview} />
            )}
          </div>
        </>
      )}
    </>
  );
}
