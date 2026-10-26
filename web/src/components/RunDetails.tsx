import { useState } from "react";
import type { Job } from "../api";
import { copyText } from "./copy";
import { LiveRegion, fmtDate, fmtNum } from "./ui";

/** What a document's run took and where it fell short, for super admins (#313): AI cost and use, pages, the limits it
 * reached, notation and why readings were refused, time by stage, and the engine's side. Everything here is already
 * recorded with the document; none of it is shown to anyone else. */

type Limit = { setting: string; value: number; wanted: number; effect: string; count?: boolean };

const STAGES: Record<string, string> = {
  validating_original: "Checking the original",
  analyzing: "Reading the layout (and OCR)",
  ai_ocr: "AI correction of OCR",
  ai_enrichment: "Music, then AI: alt text, tables, reading order, notation",
  tagging: "Tagging",
  ai_existing_figures: "AI: figures in existing tags",
  validating_output: "Checking the result",
  destructive_fixes: "Font substitution",
  flattening: "Flattening pages and tagging them again",
  reporting: "Reports",
  pdfua2: "Making the PDF/UA-2 file",
  verifying_tags: "Reviewing the tags (and AI review)",
  adobe_sending: "Sending to Adobe",
  adobe_tagging: "Adobe tagging",
};
const PARTS: Record<string, string> = { validator: "waiting for veraPDF", music: "finding and reading music" };

export function usd(n: unknown): string {
  const v = Number(n ?? 0);
  if (!Number.isFinite(v)) return "–";
  return v === 0 || Math.abs(v) >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`;
}

function num(n: unknown): string {
  return n === undefined || n === null || n === "" ? "–" : fmtNum(Number(n));
}

function Facts({ rows }: { rows: [string, string | number | null | undefined][] }) {
  const shown = rows.filter(([, v]) => v !== undefined && v !== null && v !== "");
  if (!shown.length) return null;
  return (
    <dl className="run-facts">
      {shown.map(([k, v]) => (
        <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  );
}

function Table({ caption, head, rows, numeric = [] }: {
  caption: string; head: string[]; rows: (string | number)[][]; numeric?: number[];
}) {
  return (
    <div className="table-wrap">
      <table className="data">
        <caption className="visually-hidden">{caption}</caption>
        <thead><tr>{head.map((h, i) => <th key={h} scope="col" className={numeric.includes(i) ? "num" : undefined}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, n) => (
            <tr key={n}>{r.map((c, i) => <td key={i} className={numeric.includes(i) ? "num" : undefined}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RunDetails({ job: j }: { job: Job }) {
  const c = (j.cost ?? {}) as Record<string, unknown>;
  const m = ((j.result?.metrics ?? {}) as Record<string, unknown>);
  const staff = (j.staff ?? {}) as Record<string, unknown>;
  const limits = (Array.isArray(m.limits_hit) ? m.limits_hit : []) as Limit[];
  const reasons = Object.entries((m.notation_ai_rejected_by_reason ?? {}) as Record<string, number>);
  // In the order the stages run (the database keeps a JSON object's keys in its own order)
  const order = Object.keys(STAGES);
  const at = (k: string) => (order.includes(k) ? order.indexOf(k) : order.length);
  const stages = (Object.entries((m.stage_seconds ?? {}) as Record<string, number | Record<string, number>>)
    .filter(([k, v]) => k !== "of_which" && typeof v === "number") as [string, number][])
    .sort((a, b) => at(a[0]) - at(b[0]));
  const parts = Object.entries(((m.stage_seconds as Record<string, unknown> | undefined)?.of_which ?? {}) as Record<string, number>);
  const adobe = c.adobe as Record<string, unknown> | undefined;
  const warnings = (j.result?.warnings ?? []) as string[];
  const queued = staff.submitted_at && j.created_at
    ? Math.max(0, (Date.parse(String(staff.submitted_at)) - Date.parse(j.created_at)) / 1000) : null;

  return (
    <div className="report-panel run-details">
      <p className="muted small" style={{ margin: 0 }}>Only super admins see this tab. Costs are what the service paid; people are charged pages.</p>

      <h3>AI cost and use</h3>
      <Facts rows={[
        ["AI level", j.ai_label ?? (j.no_ai ? "No AI" : null)],
        ["Total AI cost", c.ai_usd !== undefined ? usd(c.ai_usd) : null],
        ["Writing (alt text, tables, reading order, notation)", c.writer_usd !== undefined ? usd(c.writer_usd) : null],
        ["Review of the result", c.review_usd !== undefined ? usd(c.review_usd) : null],
        ["Choosing the kind of document", c.classifier_usd !== undefined ? usd(c.classifier_usd) : null],
        ["Describe with AI (tag manager)", c.describe_usd !== undefined ? `${usd(c.describe_usd)} over ${num(c.describe_calls)} uses` : null],
        ["AI calls", c.ai_calls !== undefined ? `${num(c.ai_calls)}${c.ai_failures ? ` (${num(c.ai_failures)} failed)` : ""}` : null],
        ["Tokens, writing (in / out)", c.prompt_tokens !== undefined ? `${num(c.prompt_tokens)} / ${num(c.completion_tokens)}` : null],
        ["Tokens, review (in / out)", c.review_prompt_tokens ? `${num(c.review_prompt_tokens)} / ${num(c.review_completion_tokens)}` : null],
        ["Writing model", (c.writer_model as string) ?? null],
        ["Review model", (c.review_model as string) ?? null],
        ["AI pages given back", j.ai_refunded ? (j.ai_unused ? "Yes: the AI wasn't needed" : "Yes: every AI call failed") : null],
      ]} />

      <h3>Pages</h3>
      <Facts rows={[
        ["Pages", num(j.pages)],
        ["Pages charged", num(j.pages_charged)],
        ["Charged to", j.charged_services.join(", ") || null],
        ["Sent elsewhere", j.external_services.join(", ") || null],
        ["Adobe", adobe ? `${num(adobe.pages_sent)} pages in ${num(adobe.parts)} part(s), ${num(adobe.requests)} requests, ${num(adobe.retries)} retries` : null],
      ]} />

      <h3>Limits reached</h3>
      {limits.length ? (
        <Table caption="Limits this document reached" head={["Setting", "Set to", "This document", "What happened"]} numeric={[1, 2]}
          rows={limits.map((x) => [x.setting, num(x.value), `${num(x.wanted)}${x.count ? " pages" : ""}`, x.effect])} />
      ) : <p className="muted small">None.</p>}

      <h3>Specialist notation</h3>
      <Facts rows={[
        ["Read by AI", m.notation_read_by_ai !== undefined ? num(m.notation_read_by_ai) : null],
        ["Put in words by rule (doses)", m.notation_by_rule !== undefined ? num(m.notation_by_rule) : null],
        ["AI readings refused by the checks", m.notation_ai_rejected !== undefined ? num(m.notation_ai_rejected) : null],
      ]} />
      {reasons.length > 0 && (
        <Table caption="Why AI readings were refused" head={["Why refused", "Readings"]} numeric={[1]}
          rows={reasons.sort((a, b) => b[1] - a[1]).map(([r, n]) => [r, num(n)])} />
      )}

      <h3>Other work</h3>
      <Facts rows={[
        ["Figures described", m.images_described !== undefined ? num(m.images_described) : null],
        ["Tables read by AI", m.tables_analyzed !== undefined ? num(m.tables_analyzed) : null],
        ["Pages with an AI reading order", m.reading_order_pages_ai !== undefined ? num(m.reading_order_pages_ai) : null],
        ["Pages OCR'd", m.ocr_pages ? num(m.ocr_pages) : null],
        ["OCR words corrected by AI", m.ocr_ai_words_corrected ? num(m.ocr_ai_words_corrected) : null],
        ["OCR lines written by AI", m.ocr_ai_lines_transcribed ? num(m.ocr_ai_lines_transcribed) : null],
        ["Music examples (read)", m.music_examples ? `${num(m.music_examples)} (${num(m.music_read)})` : null],
      ]} />

      <h3>Where the time went</h3>
      <Facts rows={[
        ["Waiting in the queue", queued !== null ? `${num(Math.round(queued))} s` : null],
        ["Processing", m.processing_time_seconds !== undefined ? `${num(Math.round(Number(m.processing_time_seconds)))} s` : null],
        ["Finished", j.finished_at ? fmtDate(j.finished_at) : null],
      ]} />
      {stages.length > 0 && (
        <Table caption="Time by stage" head={["Stage", "Seconds"]} numeric={[1]}
          rows={[...stages.map(([k, v]) => [STAGES[k] ?? k, v.toFixed(1)] as (string | number)[]),
            ...parts.map(([k, v]) => [`of which ${PARTS[k] ?? k}`, v.toFixed(1)])]} />
      )}

      <h3>Engine</h3>
      <Facts rows={[
        ["Engine job", (staff.engine_job_id as string) ?? null],
        ["Route", (m.route as string) ?? null],
        ["Engine version", (m.engine_version as string) ?? null],
        ["Error", j.error_code ? `${j.error_code}${j.error_message ? `: ${j.error_message}` : ""}` : null],
      ]} />
      {warnings.length > 0 && (
        <>
          <h4>Warnings</h4>
          <ul className="small">{warnings.map((w, n) => <li key={n}>{w}</li>)}</ul>
        </>
      )}
    </div>
  );
}

/** Why a document that isn't finished is where it is, for super admins (#530): its ids (the engine's names its folder
 * on the engine), what it's waiting for, with the numbers, and since when. The full Run details come when it's done. */
export function RunSoFar({ job: j }: { job: Job }) {
  const [copied, setCopied] = useState("");
  const p = (j.progress ?? {}) as Record<string, unknown>;
  const engineId = j.staff?.engine_job_id ?? null;
  const copy = async (what: string, text: string) => {
    setCopied(await copyText(text) ? `Copied the ${what}` : "Couldn't copy: select the id and copy it yourself.");
  };
  const services = (Array.isArray(p.services) ? p.services : []).join(", ");
  const why: Record<string, string> = {
    waiting_engine: `Waiting for a place at the engine: ${num(p.in_use)} of ${num(p.places)} in use`,
    waiting_per_person: `Waiting for this person's other documents: ${num(p.at_once)} at once at most`,
    waiting_at_once: `Waiting for a place on ${services}: as many documents as it takes at once`,
    waiting_daily_cap: `Waiting for tomorrow: today's page limit is reached on ${services}`,
    paused: `Paused: ${services}`,
    waiting_large: `Large document: it starts after every ${num(p.every)} smaller ones, or when nothing smaller waits`,
  };
  const stage = String(p.stage ?? "");
  const waiting = j.status === "held" ? "Waiting for the person to confirm sending it"
    : j.status === "queued" ? (why[stage] ?? "Waiting its turn") : null;
  return (
    <section className="doc-card run-so-far" aria-labelledby="run-so-far-h">
      <h2 id="run-so-far-h">Run details</h2>
      <dl className="run-facts">
        <div><dt>Document</dt><dd><code>{j.id}</code>{" "}
          <button type="button" className="btn secondary small" aria-label="Copy the document id"
            onClick={() => void copy("document id", j.id)}>Copy</button></dd></div>
        <div><dt>Engine job</dt><dd>{engineId
          ? <><code>{engineId}</code>{" "}<button type="button" className="btn secondary small"
            aria-label="Copy the engine job id" onClick={() => void copy("engine job id", engineId)}>Copy</button></>
          : <span className="muted">Not sent to the engine yet</span>}</dd></div>
        {waiting && <div><dt>Why it's waiting</dt><dd>{waiting}</dd></div>}
        <div><dt>Added</dt><dd>{fmtDate(j.created_at)}</dd></div>
        {j.staff?.submitted_at && <div><dt>Sent to the engine</dt><dd>{fmtDate(j.staff.submitted_at)}</dd></div>}
      </dl>
      <LiveRegion message={copied} />
    </section>
  );
}
