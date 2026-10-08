import { useCallback, useEffect, useRef, useState } from "react";
import { Allowances } from "../components/Allowances";
import { DocumentsList } from "../components/DocumentsList";
export { JobsTable } from "../components/JobsTable";
import { useSeen } from "../components/seen";
import { UploadForm } from "../components/UploadForm";
import { get, type ServiceChoice } from "../api";
import { useAuth } from "../auth";
import { Notice, useLoad, useTitle } from "../components/ui";

export function Home() {
  useTitle("My documents");
  const { me, reloadMe } = useAuth();
  const [listKey, setListKey] = useState(0); // a new upload starts the list afresh
  useSeen(["jobs"]);
  const choices = useLoad(() => get<ServiceChoice[]>("/services"), []);

  // Pages left change when documents are sent, finish or fail: refresh the counts whenever a document's
  // status changes (the documents list refreshes itself while any are in progress)
  const seenKey = useRef<string | null>(null);
  const onStatusChange = useCallback((key: string) => {
    if (seenKey.current !== null && seenKey.current !== key) {
      void reloadMe();
      void choices.reload();
    }
    seenKey.current = key;
  }, [reloadMe]); // eslint-disable-line react-hooks/exhaustive-deps

  // Pages and limits can change elsewhere (an admin adds pages or changes a limit): show them as they are now each
  // time the page opens, and when the person comes back to this tab
  useEffect(() => {
    void reloadMe().catch(() => {});
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        void reloadMe().catch(() => {});
        void choices.reload();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [reloadMe]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!me) return null;
  if (!me.can_submit) return <WaitingForApproval />;

  async function changed() {
    setListKey((k) => k + 1);
    await Promise.all([reloadMe(), choices.reload()]);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>My documents</h1>
          <p className="sub">{me.group_name ?? "No group"}</p>
        </div>
      </div>

      <Allowances />

      {!me.group_id && (
        <Notice kind="info">Your account isn't in a group, so it has no pages to submit. Super admins can still manage everything.</Notice>
      )}
      {me.group_id && <UploadForm choices={choices.data ?? []} onChanged={changed} />}

      <section className="panel" aria-labelledby="jobs-h">
        <h2 id="jobs-h">Your documents</h2>
        <DocumentsList key={listKey} onStatusChange={onStatusChange} />
      </section>
    </>
  );
}


/** New members of a group that approves people first: they can sign in, but not submit anything yet. */
function WaitingForApproval() {
  const { me, config, reloadMe } = useAuth();
  const [checking, setChecking] = useState(false);
  if (!me) return null;
  const contact = config.contactEmail
    ? <p>Questions? Contact <a href={`mailto:${config.contactEmail}`}>{config.contactEmail}</a>.</p> : null;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>My documents</h1>
          <p className="sub">{me.group_name ?? "No group"}</p>
        </div>
      </div>
      {me.approval === "declined" ? (
        <Notice kind="warn" title="Your request wasn't approved">
          <p>Your group's administrator declined your request to use this service, so you can't submit documents.</p>
          {me.decline_reason && <p><strong>The reason given:</strong> {me.decline_reason}</p>}
          {contact}
        </Notice>
      ) : (
        <Notice kind="info" title="Waiting for approval">
          <p>
            Your account in {me.group_name ?? "your group"} is waiting for approval by your group's administrator. You'll get
            an email when it's approved, and then you can submit documents here.
          </p>
          {contact}
          <p>
            <button className="btn secondary small" disabled={checking}
              onClick={() => { setChecking(true); void reloadMe().finally(() => setChecking(false)); }}>
              {checking ? "Checking…" : "Check again"}
            </button>
          </p>
        </Notice>
      )}
    </>
  );
}
