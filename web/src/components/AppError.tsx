import { useRouteError } from "react-router-dom";
import { isStaleBuildError } from "../staleBuild";

/** What a page shows when something goes wrong while it's being shown, instead of React's message for developers. A
 * page loaded before the service was updated asks for files that are gone: reloading fetches the new version. */
export function AppError() {
  const err = useRouteError();
  const stale = isStaleBuildError(err);
  return (
    <main id="main" className="app-error">
      <section className="panel" role="alert">
        <h1>{stale ? "The service was updated" : "Something went wrong"}</h1>
        <p>{stale
          ? "This page was opened before the update. Reload it to get the new version; nothing you saved is lost."
          : "This page couldn't be shown. Reloading it usually helps. If it keeps happening, ask for help and say "
            + "what you were doing."}</p>
        <div className="actions">
          <button type="button" className="btn" onClick={() => window.location.reload()}>Reload</button>
          <a className="btn secondary" href="/">Go to my documents</a>
        </div>
      </section>
    </main>
  );
}
