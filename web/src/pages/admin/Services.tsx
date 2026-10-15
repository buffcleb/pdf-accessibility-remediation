import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { del, get, patch, post, type Group, type Service } from "../../api";
import { ErrorNotice, Loading, fmtNum, useLoad, useTitle } from "../../components/ui";
import { useUnsavedChanges } from "../../components/unsaved";
import { modelChoices } from "./models";
import { Adjust, GatewayLink, Topup } from "./shared";

function where(s: Service) {
  if (s.engine_route === "adobe") return <span className="pill warn">External: Adobe</span>;
  return s.external ? <span className="pill warn">External</span> : <span className="muted">This service's servers</span>;
}

function status(s: Service) {
  return <span className={`pill ${s.enabled ? "good" : "muted"}`}>{s.enabled ? "On" : "Off"}</span>;
}

function pages(s: Service) {
  if (!s.metered) return "Counted only";
  return s.global_limited ? `${fmtNum(s.remaining_pages)} of ${fmtNum(s.pool_pages)} left` : "No global limit";
}

/** Every service and AI level, with its status at a glance. */
export function ServicesList() {
  useTitle("Services and AI levels");
  const services = useLoad(() => get<Service[]>("/admin/services"), []);
  if (services.loading && !services.data) return <Loading />;
  if (!services.data) return <ErrorNotice error={services.error} />;
  const processing = services.data.filter((s) => s.kind === "processing");
  const tiers = services.data.filter((s) => s.kind === "ai_tier");
  return (
    <>
      <h1>Services and AI levels</h1>
      <p className="muted">
        Status at a glance; open one to change it. A service has to be switched on here, enabled for a group and given to
        people before anyone can use it. Local-only groups never use external providers.
      </p>
      <section className="panel" aria-labelledby="proc-h">
        <h2 id="proc-h">How documents are processed</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">Service</th><th scope="col">Status</th><th scope="col">Where documents go</th>
              <th scope="col">Global pages</th><th scope="col" className="num">Sent today / daily limit</th><th scope="col" className="num">Groups</th></tr></thead>
            <tbody>
              {processing.map((s) => (
                <tr key={s.key}>
                  <td><Link to={`/admin/services/${encodeURIComponent(s.key)}`}>{s.label}</Link></td>
                  <td>{status(s)}</td><td>{where(s)}</td><td>{pages(s)}</td>
                  <td className="num">{fmtNum(s.pages_sent_today)} / {s.daily_cap_pages === null ? "none" : fmtNum(s.daily_cap_pages)}</td>
                  <td className="num">{s.groups_enabled}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel" aria-labelledby="tiers-h">
        <h2 id="tiers-h">AI levels</h2>
        <p className="small">The models come from the AI gateway, where you can also see spend, request logs and keys.{" "}
          <GatewayLink>Open the AI gateway</GatewayLink></p>
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">AI level</th><th scope="col">Status</th><th scope="col">Model / reviewer</th>
              <th scope="col">Where documents go</th><th scope="col">Pages</th><th scope="col" className="num">Groups</th></tr></thead>
            <tbody>
              {tiers.map((s) => (
                <tr key={s.key}>
                  <td><Link to={`/admin/services/${encodeURIComponent(s.key)}`}>{s.label}</Link></td>
                  <td>{status(s)}</td>
                  <td className="mono small">{s.gateway_model} / {s.gateway_judge_model ?? "default"}</td>
                  <td>{where(s)}</td><td>{s.metered ? (s.global_limited ? pages(s) : "Limited") : "Counted only"}</td>
                  <td className="num">{s.groups_enabled}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <NewAITier onCreated={services.reload} />
      </section>
    </>
  );
}

type Tab = "general" | "data" | "pages" | "model" | "kinds" | "groups";

/** One service's settings, in tabs, with one Save for all of them. */
export function ServiceDetail() {
  const { key = "" } = useParams();
  const services = useLoad(() => get<Service[]>("/admin/services"), []);
  const groups = useLoad(() => get<Group[]>("/admin/groups"), []);
  const [tab, setTab] = useState<Tab>("general");
  const [draft, setDraft] = useState<Partial<Service>>({});
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const navigate = useNavigate();
  useUnsavedChanges(Object.keys(draft).length > 0);
  const service = services.data?.find((x) => x.key === key);
  useTitle(service?.label ?? "Service");
  if (services.loading && !services.data) return <Loading />;
  if (!service) return <ErrorNotice error={services.error ?? new Error("That service doesn't exist.")} />;
  const s = { ...service, ...draft };
  const standard = service.key === "standard";
  const ai = service.kind === "ai_tier";
  const set = (p: Partial<Service>) => { setDraft({ ...draft, ...p }); setSaved(false); };
  const num = (v: string) => (v === "" ? null : Number(v));

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const updated = await patch<Service>(`/admin/services/${encodeURIComponent(service!.key)}`, draft);
      services.setData(services.data!.map((y) => (y.key === updated.key ? updated : y)));
      setDraft({});
      setSaved(true);
    } catch (err) {
      setError(err);
    }
  }

  async function remove() {
    if (!window.confirm(`Delete "${service!.label}"? Its kinds of document and every group's settings and pages for it are `
      + "deleted with it. Documents already processed keep their record. This can't be undone.")) return;
    setError(null);
    try {
      await del(`/admin/services/${encodeURIComponent(service!.key)}`);
      navigate("/admin/services");
    } catch (err) {
      setError(err);
    }
  }

  const tabs: [Tab, string][] = [["general", "General"], ["data", "Data and notice"], ["pages", "Pages and cost guards"],
    ...(ai ? [["model", "Model"] as [Tab, string], ["kinds", "Kinds of document"] as [Tab, string]]
      : service.engine_route === "adobe" ? [["model", "Adobe connection"] as [Tab, string]] : []),
    ["groups", "Groups"]];
  const using = (groups.data ?? []).map((g) => ({ g, gs: g.services.find((x) => x.key === service.key) }));

  return (
    <>
      <p className="small"><Link to="/admin/services">← Services and AI levels</Link></p>
      <div className="page-head">
        <div>
          <h1>{service.label}</h1>
          <p className="sub">
            {service.enabled ? "On" : "Off"} · {service.engine_route === "adobe" ? "external (Adobe)" : service.external ? "external provider" : "this service's servers"}
            {" "}· {service.groups_enabled} group{service.groups_enabled === 1 ? "" : "s"} · {fmtNum(service.used_pages)} pages used
            {" "}· {fmtNum(service.pages_sent_today)} sent today
          </p>
        </div>
      </div>
      <div className="tabs" role="tablist" aria-label="Service settings">
        {tabs.map(([t, label]) => (
          <button key={t} className="tab" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{label}</button>
        ))}
      </div>
      {tab === "kinds" && ai && <KindsPanel service={service}
        onChange={(updated) => services.setData(services.data!.map((y) => (y.key === updated.key ? updated : y)))} />}
      {tab !== "kinds" && <form className="panel" onSubmit={save} role="tabpanel">
        {tab === "general" && (
          <>
            <label className="check"><input type="checkbox" checked={s.enabled} disabled={standard} onChange={(e) => set({ enabled: e.target.checked })} />
              <span>Switched on{standard && <span className="muted small"> (the standard service is always on)</span>}</span></label>
            <div className="field"><label htmlFor="sd-label">Name shown to people</label>
              <input id="sd-label" type="text" value={s.label} onChange={(e) => set({ label: e.target.value })} /></div>
            <div className="field"><label htmlFor="sd-desc">Description <span className="hint">(shown at upload)</span></label>
              <input id="sd-desc" type="text" value={s.description ?? ""} onChange={(e) => set({ description: e.target.value || null })} /></div>
            <div className="field" style={{ maxWidth: 200 }}><label htmlFor="sd-sort">Order in lists <span className="hint">(lower first)</span></label>
              <input id="sd-sort" type="number" min={0} value={s.sort} onChange={(e) => set({ sort: Number(e.target.value) })} /></div>
          </>
        )}
        {tab === "data" && (
          <>
            <label className="check"><input type="checkbox" checked={s.external} disabled={standard || service.engine_route === "adobe"}
              onChange={(e) => set({ external: e.target.checked })} />
              <span>Sends documents to an external provider <span className="muted small">(blocked for local-only groups{service.engine_route === "adobe" ? "; always on for Adobe" : ""})</span></span></label>
            <label className="check"><input type="checkbox" checked={s.notice_enabled} onChange={(e) => set({ notice_enabled: e.target.checked })} />
              <span>Show a notice when people pick it</span></label>
            {s.notice_enabled && (
              <div className="field"><label htmlFor="sd-notice">Notice</label>
                <textarea id="sd-notice" value={s.notice_text ?? ""} onChange={(e) => set({ notice_text: e.target.value || null })} /></div>
            )}
          </>
        )}
        {tab === "pages" && (
          <>
            <label className="check"><input type="checkbox" checked={s.metered} onChange={(e) => set({ metered: e.target.checked })} />
              <span>Page limits apply <span className="muted small">(otherwise use is only counted)</span></span></label>
            <div className="form-row">
              <div className="field"><label htmlFor="sd-job">Most pages per document <span className="hint">(empty = no limit)</span></label>
                <input id="sd-job" type="number" min={1} value={s.max_pages_per_job ?? ""} onChange={(e) => set({ max_pages_per_job: num(e.target.value) })} /></div>
              <div className="field"><label htmlFor="sd-day">Most pages sent per day <span className="hint">(empty = no limit; extra documents wait)</span></label>
                <input id="sd-day" type="number" min={0} value={s.daily_cap_pages ?? ""} onChange={(e) => set({ daily_cap_pages: num(e.target.value) })} /></div>
              <div className="field"><label htmlFor="sd-once">Most documents at once <span className="hint">(empty = no limit; extra documents wait.
                Set it to what can't grow with the rest: a local AI's GPUs, a provider's rate limit)</span></label>
                <input id="sd-once" type="number" min={1} value={s.max_at_once ?? ""} onChange={(e) => set({ max_at_once: num(e.target.value) })} /></div>
            </div>
            <label className="check"><input type="checkbox" checked={s.confirm_before_send} onChange={(e) => set({ confirm_before_send: e.target.checked })} />
              <span>Ask people to confirm each document, with its page count, before it's sent</span></label>
            {ai && (
              <fieldset className="choices">
                <legend>Audits <span className="muted small">(Check only: a report without changing the file)</span></legend>
                {([["free", "Use AI, free", "The AI review runs and no pages are counted."],
                  ["charge", "Use AI and count its pages", "Each page counts against this level's pages (not the processing pages)."],
                  ["off", "No AI", "Audits are made without AI and use no pages."]] as const).map(([v, l, d]) => (
                  <label className="check" key={v}>
                    <input type="radio" name="sd-audit" checked={s.audit_ai === v} onChange={() => set({ audit_ai: v })} />
                    <span>{l} <span className="muted small">{d}</span></span>
                  </label>
                ))}
              </fieldset>
            )}
            <details>
              <summary>Global page pool {service.global_limited ? `(${fmtNum(service.remaining_pages)} of ${fmtNum(service.pool_pages)} left)` : "(off)"}</summary>
              <label className="check"><input type="checkbox" checked={s.global_limited} onChange={(e) => set({ global_limited: e.target.checked })} />
                <span>Cap the whole service with a global pool, on top of group pools <span className="muted small">(starts empty; top-ups need a ticket)</span></span></label>
            </details>
          </>
        )}
        {tab === "model" && ai && (
          <>
            <ModelFields model={s.gateway_model} judge={s.gateway_judge_model} onChange={set} />
            <label className="check"><input type="checkbox" checked={s.allow_full_review} onChange={(e) => set({ allow_full_review: e.target.checked })} />
              <span>Allow "review every figure" <span className="muted small">(more AI calls per document)</span></span></label>
            <label className="check"><input type="checkbox" checked={s.allow_notation} onChange={(e) => set({ allow_notation: e.target.checked })} />
              <span>Read specialist notation with AI <span className="muted small">(each reading is flagged for review)</span></span></label>
            <label className="check"><input type="checkbox" checked={s.allow_ocr_ai} onChange={(e) => set({ allow_ocr_ai: e.target.checked })} />
              <span>Correct OCR with AI <span className="muted small">(each change is flagged for review)</span></span></label>
          </>
        )}
        {tab === "model" && service.engine_route === "adobe" && (
          <p>
            The engine connects to Adobe PDF Services with its own credentials and rate limits (set on the engine; see the
            operations guide). Use this service's daily limit and most pages per document to control Adobe spend.
          </p>
        )}
        {tab === "groups" && (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Group</th><th scope="col">For the group</th><th scope="col">Who has it</th><th scope="col">Group's pages</th></tr></thead>
              <tbody>
                {using.map(({ g, gs }) => (
                  <tr key={g.id}>
                    <td><Link to={`/admin/groups/${g.id}/services`}>{g.name}</Link></td>
                    <td>{gs?.enabled ? <span className="pill good">On</span> : <span className="pill muted">Off</span>}</td>
                    <td>{gs?.enabled ? (gs.all_users ? "Everyone" : `${gs.people_with_access} chosen`) : "–"}</td>
                    <td>{!gs?.enabled ? "–" : !service.metered ? "Counted only"
                      : <span className={gs.balance.remaining_pages <= 0 ? "bad-text" : undefined}>{fmtNum(gs.balance.remaining_pages)} of {fmtNum(gs.balance.pool_pages)} left</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {tab !== "groups" && !(tab === "model" && !ai) && (
          <div className="actions">
            <button className="btn" type="submit" disabled={Object.keys(draft).length === 0}>Save</button>
            {Object.keys(draft).length > 0 && <span className="small unsaved-note">Unsaved changes (in any tab)</span>}
            {saved && <span role="status" className="muted">Saved.</span>}
          </div>
        )}
        <ErrorNotice error={error} />
      </form>}
      {tab === "general" && ai && (
        <div className="panel">
          <h3>Delete this AI level</h3>
          <p className="muted">
            {service.enabled ? "Switch it off and save first. " : ""}It can be deleted once no document is waiting or in
            progress with it. Documents already processed keep their record; reports show them under <span className="mono">{service.key}</span>.
          </p>
          <button className="btn secondary danger-text" type="button" disabled={service.enabled || Object.keys(draft).length > 0}
            onClick={() => void remove()}>Delete AI level</button>
        </div>
      )}
      {tab === "pages" && service.global_limited && (
        <>
          <Topup base={`/admin/services/${encodeURIComponent(service.key)}`} fixedService={service.key} onDone={services.reload}
            title="Add pages to the global pool" />
          <Adjust base={`/admin/services/${encodeURIComponent(service.key)}`} fixedService={service.key} onDone={services.reload} />
        </>
      )}
    </>
  );
}

/** Kinds of document within an AI level: each with the gateway models that suit it. People choose the kind at
 * upload by its name and description; they never see the models. General is the level's own models. */
function KindsPanel({ service, onChange }: { service: Service; onChange: (s: Service) => void }) {
  const models = useAIModels();
  const [label, setLabel] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState("");
  const [judge, setJudge] = useState("");
  const [error, setError] = useState<unknown>(null);
  const base = `/admin/services/${encodeURIComponent(service.key)}/kinds`;

  async function act(fn: () => Promise<Service>) {
    setError(null);
    try {
      onChange(await fn());
      return true;
    } catch (e) {
      setError(e);
      return false;
    }
  }
  async function add(e: FormEvent) {
    e.preventDefault();
    if (await act(() => post<Service>(base, { label, description: description || null, gateway_model: model,
      gateway_judge_model: judge || null }))) {
      setLabel(""); setDescription(""); setModel(""); setJudge("");
    }
  }
  const options = models.data?.models ?? [];
  return (
    <div className="panel" role="tabpanel">
      <p className="muted">People pick a kind at upload when there's more than General, by its name and description. They
        never see the models. If a kind's model isn't available or fails, the document is processed with General.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th scope="col">Kind</th><th scope="col">Model</th><th scope="col">Review model</th>
            <th scope="col">Offered</th><th scope="col"><span className="visually-hidden">Remove</span></th></tr></thead>
          <tbody>
            <tr>
              <td><strong>General</strong><div className="muted small">Always offered</div></td>
              <td className="mono small">{service.gateway_model}</td>
              <td className="mono small">{service.gateway_judge_model ?? "default"}</td>
              <td>Yes</td>
              <td className="muted small">Set on the Model tab</td>
            </tr>
            {service.kinds.map((k) => (
              <tr key={k.key}>
                <td><strong>{k.label}</strong>{k.description && <div className="muted small">{k.description}</div>}</td>
                <td>
                  <select aria-label={`Model for ${k.label}`} value={k.gateway_model}
                    onChange={(e) => void act(() => patch<Service>(`${base}/${k.key}`, { gateway_model: e.target.value }))}>
                    {[...new Set([k.gateway_model, ...options])].map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </td>
                <td>
                  <select aria-label={`Review model for ${k.label}`} value={k.gateway_judge_model ?? ""}
                    onChange={(e) => void act(() => patch<Service>(`${base}/${k.key}`, { gateway_judge_model: e.target.value || null }))}>
                    <option value="">The level's</option>
                    {[...new Set([...(k.gateway_judge_model ? [k.gateway_judge_model] : []), ...options])]
                      .map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </td>
                <td><input type="checkbox" aria-label={`Offer ${k.label}`} checked={k.enabled}
                  onChange={(e) => void act(() => patch<Service>(`${base}/${k.key}`, { enabled: e.target.checked }))} /></td>
                <td><button type="button" className="btn secondary small" onClick={() => {
                  if (window.confirm(`Remove "${k.label}"? Documents already waiting for it use General.`)) {
                    void act(() => del<Service>(`${base}/${k.key}`).then((x) => x!));
                  }
                }}>Remove<span className="visually-hidden"> {k.label}</span></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>Add a kind of document</h3>
      {models.error ? <ErrorNotice error={models.error} /> : null}
      <form className="form-row" onSubmit={add}>
        <div className="field"><label htmlFor="k-label">Name shown to people</label>
          <input id="k-label" type="text" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Math and science" /></div>
        <div className="field" style={{ flex: "2 1 260px" }}><label htmlFor="k-desc">Description <span className="hint">(shown at upload)</span></label>
          <input id="k-desc" type="text" value={description} onChange={(e) => setDescription(e.target.value)}
            placeholder="e.g. Equations, formulas, chemistry and charts" /></div>
        <div className="field"><label htmlFor="k-model">Model</label>
          <select id="k-model" value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">Choose…</option>
            {options.map((m) => <option key={m} value={m}>{m}</option>)}
          </select></div>
        <div className="field"><label htmlFor="k-judge">Review model <span className="hint">(empty = the level's)</span></label>
          <select id="k-judge" value={judge} onChange={(e) => setJudge(e.target.value)}>
            <option value="">The level's</option>
            {options.filter((m) => m !== model).map((m) => <option key={m} value={m}>{m}</option>)}
          </select></div>
        <button className="btn" type="submit" disabled={!label.trim() || !model}>Add kind</button>
      </form>
      <ErrorNotice error={error} />
    </div>
  );
}

/** An AI level's writer and review model, chosen from what the gateway serves. */
function ModelFields({ model, judge, onChange }: {
  model: string | null; judge: string | null; onChange: (p: Partial<Service>) => void;
}) {
  const models = useAIModels();
  const served = models.data?.models ?? [];
  return (
    <>
      {models.error ? <ErrorNotice error={models.error} /> : null}
      <div className="field" style={{ maxWidth: 360 }}><label htmlFor="sd-model">Gateway model</label>
        <select id="sd-model" value={model ?? ""} onChange={(e) => onChange({ gateway_model: e.target.value })}>
          {modelChoices(model, served).map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select></div>
      <div className="field" style={{ maxWidth: 420 }}><label htmlFor="sd-judge">Review model <span className="hint">(a different model family)</span></label>
        <select id="sd-judge" value={judge ?? ""} onChange={(e) => onChange({ gateway_judge_model: e.target.value || null })}>
          <option value="">Default review model</option>
          {modelChoices(judge, served, model).map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select></div>
    </>
  );
}

function useAIModels() {
  return useLoad(() => get<{ models: string[] }>("/admin/ai-models"), []);
}

function NewAITier({ onCreated }: { onCreated: () => void }) {
  const models = useAIModels();
  const [label, setLabel] = useState("");
  const [model, setModel] = useState("");
  const [judge, setJudge] = useState("");
  const [external, setExternal] = useState(false);
  const [metered, setMetered] = useState(true);
  const [error, setError] = useState<unknown>(null);
  useUnsavedChanges(!!(label.trim() || model));

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await post("/admin/services", { label, gateway_model: model, gateway_judge_model: judge || null, external, metered });
      setLabel(""); setModel(""); setJudge("");
      onCreated();
    } catch (err) {
      setError(err);
    }
  }
  return (
    <details className="panel">
      <summary>Add an AI level</summary>
      <p className="muted small">
        An AI level offers one of the AI gateway's models (set up in config/litellm.yaml). New ones start switched off.
      </p>
      {models.error ? <ErrorNotice error={models.error} /> : null}
      <form className="form-row" onSubmit={create}>
        <div className="field">
          <label htmlFor="tier-label">Name shown to people</label>
          <input id="tier-label" type="text" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Commercial AI" />
        </div>
        <div className="field">
          <label htmlFor="tier-model">Gateway model</label>
          <select id="tier-model" value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">Choose…</option>
            {models.data?.models.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="tier-judge">Review model <span className="hint">(a different model family; empty = the default)</span></label>
          <select id="tier-judge" value={judge} onChange={(e) => setJudge(e.target.value)}>
            <option value="">Default review model</option>
            {models.data?.models.filter((m) => m !== model).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <label className="check"><input type="checkbox" checked={external} onChange={(e) => setExternal(e.target.checked)} /><span>External provider</span></label>
        <label className="check"><input type="checkbox" checked={metered} onChange={(e) => setMetered(e.target.checked)} /><span>Page limits apply</span></label>
        <button className="btn" type="submit" disabled={!label.trim() || !model}>Add AI level</button>
      </form>
      <ErrorNotice error={error} />
    </details>
  );
}
