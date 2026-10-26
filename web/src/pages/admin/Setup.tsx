import { useState } from "react";
import { Link } from "react-router-dom";
import { get, patch, post, put, type AccessCheck, type Group, type Service, type User } from "../../api";
import { useAuth } from "../../auth";
import { acrossGroups, can } from "../../permissions";
import { AccessCheckView } from "../../components/attention";
import { ErrorNotice, Loading, Notice, fmtNum, useLoad, useTitle } from "../../components/ui";
import { useUnsavedChanges } from "../../components/unsaved";

const STEPS = ["Service and group", "Who gets it", "Pages", "Default limit", "Check and finish"];

/** Guided set-up: offer a service to a group, in the order that works (switch it on, give it to people, put pages in
 * the pool, set the default limit), then check a member can actually use it. */
export function OfferService() {
  useTitle("Offer a service to a group");
  const { me, config } = useAuth();
  // Every group and service (super admins, service admins); switching a service on for everyone (super admins);
  // putting pages in the pool (both)
  const across = acrossGroups(me);
  const switches = !!me?.is_super_admin;
  const addsPages = can(me, "pages");
  const groups = useLoad(() => get<Group[]>(across ? "/admin/groups" : "/groups"), [across]);
  const services = useLoad(() => (across ? get<Service[]>("/admin/services") : Promise.resolve(null)), [across]);
  const [step, setStep] = useState(0);
  const [groupId, setGroupId] = useState("");
  const [key, setKey] = useState("");
  const [switchOn, setSwitchOn] = useState(true);
  const [everyone, setEveryone] = useState(true);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [pages, setPages] = useState("");
  const [ticket, setTicket] = useState("");
  const [cap, setCap] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<{ check: AccessCheck | null; checkError: unknown } | null>(null);
  useUnsavedChanges(!done && (step > 0 || key !== ""));
  const onlyGroup = !across && groups.data?.length === 1 ? groups.data[0].id : "";
  const memberGroup = groupId || onlyGroup;
  const members = useLoad(() => (memberGroup ? get<User[]>(`/groups/${memberGroup}/users?limit=1000`) : Promise.resolve([] as User[])), [memberGroup]);

  if (groups.loading && !groups.data) return <Loading />;
  if (!groups.data) return <ErrorNotice error={groups.error} />;
  const group = groups.data.find((g) => g.id === groupId) ?? (!across && groups.data.length === 1 ? groups.data[0] : null);
  const gs = group?.services.find((x) => x.key === key) ?? null;
  const svcList = across ? (services.data ?? []).map((s) => ({ key: s.key, label: s.label, on: s.enabled, external: s.external, metered: s.metered }))
    : (group?.services ?? []).filter((x) => x.globally_enabled).map((x) => ({ key: x.key, label: x.label, on: x.globally_enabled, external: x.external, metered: x.metered }));
  const svc = svcList.find((x) => x.key === key) ?? null;
  const blocked = !!(svc?.external && group?.local_only);
  const active = (members.data ?? []).filter((u) => u.status === "active");
  const people = everyone ? active.length : chosen.size;
  const pool = (gs?.balance.remaining_pages ?? 0) + (addsPages ? Number(pages || 0) : 0);
  const capN = cap === "" ? null : Number(cap);
  const canNext = step === 0 ? !!(group && svc && !blocked && (svc.on || (switches && switchOn)))
    : step === 1 ? everyone || chosen.size > 0
      : step === 2 ? !addsPages || pages === "" || (Number(pages) > 0 && ticket.trim().length > 0)
        : true;

  async function finish() {
    if (!group || !svc) return;
    setBusy(true);
    setError(null);
    try {
      if (switches && !svc.on) await patch(`/admin/services/${encodeURIComponent(svc.key)}`, { enabled: true });
      const body: Record<string, unknown> = { enabled: true, all_users: everyone };
      if (svc.metered) body.default_user_cap = capN;
      await patch(`/groups/${group.id}/services/${encodeURIComponent(svc.key)}`, body);
      if (!everyone) {
        for (const id of chosen) await put(`/groups/${group.id}/users/${id}/services/${encodeURIComponent(svc.key)}`, { granted: true });
      }
      if (addsPages && svc.metered && Number(pages) > 0) {
        await post(`/admin/groups/${group.id}/topups`, { service: svc.key, pages: Number(pages), ticket_ref: ticket.trim() });
      }
      const sample = everyone ? active[0] : active.find((u) => chosen.has(u.id));
      // The set-up is saved by now; a failed check mustn't look like a failed set-up.
      let check: AccessCheck | null = null;
      let checkError: unknown = null;
      try {
        if (sample) check = await get<AccessCheck>(`/groups/${group.id}/access-check?email=${encodeURIComponent(sample.email)}&service=${encodeURIComponent(svc.key)}`);
      } catch (e) {
        checkError = e;
      }
      setDone({ check, checkError });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  if (done && group && svc) {
    return (
      <>
        <h1>Offer a service to a group</h1>
        <Notice kind="good" title={`${svc.label} is set up for ${group.name}`}>
          <span>Here's the check for one of the people who now have it:</span>
        </Notice>
        {done.check ? <AccessCheckView result={done.check} />
          : done.checkError ? <><p className="muted">Everything is saved, but the check couldn't run:</p><ErrorNotice error={done.checkError} /></>
            : <p className="muted">The group has no active members to check yet.</p>}
        <p><Link className="btn" to={`/admin/groups/${group.id}/services`}>Open {group.name}'s services and pages</Link></p>
      </>
    );
  }

  return (
    <>
      <h1>Offer a service to a group</h1>
      <ol className="wizard-steps" aria-label="Steps">
        {STEPS.map((s, i) => (
          <li key={s} className={i < step ? "done" : undefined} aria-current={i === step ? "step" : undefined}>{i + 1}. {s}</li>
        ))}
      </ol>
      <section className="panel" aria-labelledby="wiz-h">
        <h2 id="wiz-h">{STEPS[step]}</h2>
        {step === 0 && (
          <>
            <div className="form-row">
              <div className="field"><label htmlFor="w-group">Group</label>
                <select id="w-group" value={group?.id ?? ""} onChange={(e) => { setGroupId(e.target.value); setChosen(new Set()); }}>
                  <option value="">Choose…</option>
                  {groups.data.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                </select></div>
              <div className="field"><label htmlFor="w-svc">Service</label>
                <select id="w-svc" value={key} onChange={(e) => setKey(e.target.value)} disabled={!group && !across}>
                  <option value="">Choose…</option>
                  {svcList.filter((x) => x.key !== "standard").map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
                </select></div>
            </div>
            {group && !across && svcList.filter((x) => x.key !== "standard").length === 0 && (
              <Notice kind="info">No other services are switched on yet. A super admin switches services on; ask them if your group needs one.</Notice>)}
            {svc && !svc.on && (switches
              ? <label className="check"><input type="checkbox" checked={switchOn} onChange={(e) => setSwitchOn(e.target.checked)} />
                <span>{svc.label} is switched off for everyone: switch it on</span></label>
              : <Notice kind="warn">{svc.label} is switched off for everyone. A super admin has to switch it on first.</Notice>)}
            {blocked && <Notice kind="warn" title="Not allowed for this group">{group!.name} is local-only, and {svc!.label} sends documents to an external provider. Turn off local-only in the group's settings first, if its documents may go there.</Notice>}
            {svc && group && !blocked && <p className="muted small">{svc.external ? `${svc.label} sends documents to an external provider; ${group.name} isn't local-only, so it can use it.` : `${svc.label} runs on this service's own servers.`}
              {gs?.enabled ? ` It's already on for ${group.name}; you can still change who has it, its pages and its default limit.` : ""}</p>}
          </>
        )}
        {step === 1 && (
          <>
            <label className="check"><input type="radio" name="w-who" checked={everyone} onChange={() => setEveryone(true)} />
              <span>Everyone in {group!.name} ({active.length} {active.length === 1 ? "person" : "people"})</span></label>
            <label className="check"><input type="radio" name="w-who" checked={!everyone} onChange={() => setEveryone(false)} /><span>Chosen people</span></label>
            {!everyone && (
              <fieldset className="choices"><legend>People</legend>
                {active.map((u) => (
                  <label key={u.id} className="check"><input type="checkbox" checked={chosen.has(u.id)}
                    onChange={(e) => { const n = new Set(chosen); if (e.target.checked) n.add(u.id); else n.delete(u.id); setChosen(n); }} />
                    <span>{u.email}</span></label>
                ))}
              </fieldset>
            )}
          </>
        )}
        {step === 2 && (!svc!.metered ? <p>{svc!.label} has no page limits: use is counted, never limited. Nothing to add.</p> : addsPages ? (
          <>
            <p>A group needs pages in its pool, or everyone sees 0 left whatever their limit. {group!.name} has {fmtNum(gs?.balance.remaining_pages ?? 0)} {svc!.label} pages now.</p>
            <div className="form-row">
              <div className="field"><label htmlFor="w-pages">Pages to add <span className="hint">(empty = none now)</span></label>
                <input id="w-pages" type="number" min={1} value={pages} onChange={(e) => setPages(e.target.value)} /></div>
              <div className="field"><label htmlFor="w-ticket">Approving ticket number</label>
                <input id="w-ticket" type="text" value={ticket} disabled={!pages} onChange={(e) => setTicket(e.target.value)} /></div>
            </div>
          </>
        ) : (
          <>
            <p>{group!.name} has {fmtNum(gs?.balance.remaining_pages ?? 0)} {svc!.label} pages. Only super admins add pages, under an approving ticket.</p>
            {config.supportUrl && <p><a href={config.supportUrl} target="_blank" rel="noreferrer noopener">Request pages<span className="visually-hidden"> (opens in a new tab)</span></a></p>}
          </>
        ))}
        {step === 3 && (!svc!.metered ? <p>No page limits for {svc!.label}.</p> : (
          <>
            <div className="field" style={{ maxWidth: 300 }}><label htmlFor="w-cap">Pages per person <span className="hint">(empty = no personal limit)</span></label>
              <input id="w-cap" type="number" min={0} value={cap} onChange={(e) => setCap(e.target.value)} /></div>
            {capN !== null && people * capN > pool && (
              <p className="muted small">{people} {people === 1 ? "person" : "people"} × {fmtNum(capN)} = {fmtNum(people * capN)}, more than the {fmtNum(pool)} pages in the pool: the pool runs out first. That's fine, just expected.</p>
            )}
          </>
        ))}
        {step === 4 && (
          <ul>
            {switches && !svc!.on && <li>Switch {svc!.label} on for everyone</li>}
            <li>{gs?.enabled ? "Keep" : "Turn"} {svc!.label} on for {group!.name}, for {everyone ? "everyone" : `${chosen.size} chosen ${chosen.size === 1 ? "person" : "people"}`}</li>
            {addsPages && svc!.metered && Number(pages) > 0 && <li>Add {fmtNum(Number(pages))} pages to the group's pool (ticket {ticket})</li>}
            {svc!.metered && <li>Default limit: {capN === null ? "no personal limit" : `${fmtNum(capN)} pages per person`}</li>}
            <li>Then check that one of them can use it</li>
          </ul>
        )}
        <ErrorNotice error={error} />
        <div className="actions" style={{ justifyContent: "space-between" }}>
          <button className="btn secondary" type="button" disabled={step === 0 || busy} onClick={() => setStep(step - 1)}>Back</button>
          {step < STEPS.length - 1
            ? <button className="btn" type="button" disabled={!canNext} onClick={() => setStep(step + 1)}>Next</button>
            : <button className="btn" type="button" disabled={busy} onClick={() => void finish()}>{busy ? "Saving…" : "Save all"}</button>}
        </div>
      </section>
    </>
  );
}
