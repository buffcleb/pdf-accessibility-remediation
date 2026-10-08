import type { CSSProperties } from "react";
import { useAuth } from "../auth";
import { type UserService } from "../api";
import { fmtNum } from "./ui";

const LOW = 0.1; // under a tenth left: "Low"

type Row = { key: string; label: string; left: number | null; used: number; inProgress: number; limit: number | null; total: number };

/** The pages someone can still use, per service, as one compact row: each allowance's background fills with the
 * share that's left, and the words always say it (color and fill are only a hint). Details opens the breakdown. */
export function Allowances() {
  const { me, config } = useAuth();
  if (!me || !me.group_id) return null;
  const rows: Row[] = me.services.filter((s: UserService) => s.available).map((s) => {
    const used = s.used_pages, inProgress = s.reserved_pages, left = s.remaining_pages;
    const total = s.effective_cap ?? (left === null ? 0 : used + inProgress + left);
    return { key: s.key, label: s.label, left, used, inProgress, limit: s.effective_cap, total };
  });
  if (rows.length === 0) return null;
  const share = (r: Row) => (r.left === null || r.total <= 0 ? 1 : Math.max(0, Math.min(1, r.left / r.total)));
  const state = (r: Row) => (r.left === null ? "unlimited" : r.left <= 0 ? "empty" : share(r) < LOW ? "low" : "ok");
  const short = rows.some((r) => state(r) === "empty" || state(r) === "low");

  return (
    <section className="allowances" aria-labelledby="allow-h">
      <h2 id="allow-h" className="visually-hidden">Pages you can still use</h2>
      <ul>
        {rows.map((r) => {
          const st = state(r);
          return (
            <li key={r.key} className={`allowance ${st}`} style={{ "--left": `${Math.round(share(r) * 100)}%` } as CSSProperties}>
              <span className="allowance-name">{r.label}</span>
              <span className="allowance-line">
              {r.left === null ? <span className="allowance-num">No limit</span> : (
                <span className="allowance-num" role="meter" aria-label={`${r.label} pages left`} aria-valuemin={0}
                  aria-valuemax={r.total} aria-valuenow={r.left}
                  aria-valuetext={r.left <= 0 ? "No pages left" : r.total > r.left
                    ? `${fmtNum(r.left)} of ${fmtNum(r.total)} pages left` : `${fmtNum(r.left)} pages left`}>
                  <strong>{fmtNum(r.left)}</strong> pages left
                </span>
              )}
              {st === "low" && <span className="allowance-flag"><span aria-hidden="true">▲</span> Low</span>}
              {st === "empty" && <span className="allowance-flag"><span aria-hidden="true">■</span> None left</span>}
              </span>
              <span className="allowance-bar" aria-hidden="true" />
            </li>
          );
        })}
      </ul>
      <div className="allowance-more">
          <details>
            <summary>Details</summary>
            <div className="allowance-details">
              <table>
                <thead><tr><th scope="col">Processing</th><th scope="col" className="num">Used</th>
                  <th scope="col" className="num">In progress</th><th scope="col" className="num">Your limit</th>
                  <th scope="col" className="num">Left</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.key}>
                      <th scope="row">{r.label}</th>
                      <td className="num">{fmtNum(r.used)}</td>
                      <td className="num">{fmtNum(r.inProgress)}</td>
                      <td className="num">{r.limit === null ? "None" : fmtNum(r.limit)}</td>
                      <td className="num">{r.left === null ? "No limit" : fmtNum(r.left)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted small">Pages left can be fewer than your limit allows when your group's pages are running out.</p>
            </div>
          </details>
      </div>
      {short && (
        <p className="allowance-note small">
          Need more pages? Ask your group's administrator{config.supportUrl ? <>, or <a href={config.supportUrl}>contact support</a></> : ""}.
        </p>
      )}
    </section>
  );
}
