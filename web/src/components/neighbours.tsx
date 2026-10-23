import { Link, useLocation } from "react-router-dom";
import { get } from "../api";
import { atPage, listSource } from "./listQuery";
import { fmtNum, useLoad } from "./ui";

type Neighbour = { id: string; filename: string };
type Neighbours = { position: number | null; total: number; previous: Neighbour | null; next: Neighbour | null };

/** Previous and Next on a document's page, in the order and with the filters of the list it was opened from, across
 * that list's pages (the API finds the neighbours). Real links, styled as the list's pager is (#506), named by the
 * document they open (heard by screen readers, shown as a tooltip); at the ends there's no link, never a link to
 * nothing. Nothing is shown when the document is no longer in the list. */
export function DocumentNav({ jobId, from }: { jobId: string; from: string }) {
  const location = useLocation();
  const source = listSource(from);
  const n = useLoad(() => (source ? get<Neighbours>(`${source.api}/neighbours/${jobId}?${source.query}`) : Promise.resolve(null)),
    [jobId, from]);
  const d = n.data;
  if (!source || !d || d.position === null || d.total < 2) return null;
  const to = (x: Neighbour, position: number) => `/jobs/${x.id}?from=${encodeURIComponent(atPage(from, source, position))}`;
  return (
    <nav className="doc-nav" aria-label={`Documents in ${source.label}`}>
      {d.previous && <Link className="btn secondary small" rel="prev" to={to(d.previous, d.position - 1)}
        state={location.state} title={d.previous.filename} aria-label={`Previous: ${d.previous.filename}`}>
        <span aria-hidden="true">←</span> Previous</Link>}
      <span className="small muted">{fmtNum(d.position)} of {fmtNum(d.total)}</span>
      {d.next && <Link className="btn secondary small" rel="next" to={to(d.next, d.position + 1)}
        state={location.state} title={d.next.filename} aria-label={`Next: ${d.next.filename}`}>
        Next <span aria-hidden="true">→</span></Link>}
    </nav>
  );
}
