import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { FullWidth } from "./fullwidth";
import { useConfirmLeave } from "./unsaved";

export function Layout() {
  const { me, config, signOut } = useAuth();
  const location = useLocation();
  const mainRef = useRef<HTMLElement>(null);
  const navigate = useNavigate();
  const confirmLeave = useConfirmLeave();
  // Full width for wide content; back to normal on another page
  const [full, setFull] = useState(false);
  useEffect(() => { setFull(false); }, [location.pathname]);
  const fullWidth = useMemo(() => ({ full, setFull }), [full]);

  // Keep the badges (new replies, people waiting, requests waiting on us) current
  const { reloadMe } = useAuth();
  useEffect(() => {
    const t = window.setInterval(() => { if (!document.hidden) void reloadMe().catch(() => {}); }, 60000);
    return () => window.clearInterval(t);
  }, [reloadMe]);

  // Move focus to the page content on navigation, so screen reader users hear the new page
  useEffect(() => {
    mainRef.current?.focus({ preventScroll: true });
  }, [location.pathname]);

  // The header stays in view while the page scrolls. Its height changes (it wraps on a narrow window), and what
  // sticks under it, or is scrolled into view, has to clear it: the height is kept where the styles can read it.
  const barRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const bar = barRef.current;
    if (!bar) return;
    const note = () => document.documentElement.style.setProperty("--topbar-h", `${Math.round(bar.getBoundingClientRect().height)}px`);
    note();
    const seen = new ResizeObserver(note);
    seen.observe(bar);
    return () => seen.disconnect();
  }, [me !== null]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (!me) return null;
  const isAdmin = me.is_super_admin || me.admin_of.length > 0;

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar" ref={barRef}>
        <div className="topbar-inner">
          <NavLink to="/" className="brand">
            {config.logoUrl
              ? <img src={config.logoUrl} alt={config.logoAlt || config.organization || ""} className="brand-logo" />
              : <img src="/favicon.svg" alt="" />}
            <span>{config.appName}</span>
          </NavLink>
          <nav className="nav" aria-label="Main">
            <NavLink to="/" end>
              My documents
              {me.help_unread > 0 && <> <span className="badge">{me.help_unread}<span className="visually-hidden"> new {me.help_unread === 1 ? "reply" : "replies"} on your help requests</span></span></>}
            </NavLink>
            {isAdmin && (
              <NavLink to="/admin">Administration
                {me.pending_approvals > 0 && <> <span className="badge">{me.pending_approvals}<span className="visually-hidden"> waiting for approval</span></span></>}
              </NavLink>
            )}
          </nav>
          <div className="who">
            <span>{me.email}</span>
            <NavLink to="/settings" className="btn secondary small">Settings</NavLink>
            <button className="btn secondary small"
              onClick={async () => {
                if (!(await confirmLeave())) return;
                await signOut();
                navigate("/login", { replace: true, state: null });
              }}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main id="main" ref={mainRef} tabIndex={-1} className={location.pathname.endsWith("/tags") ? "wide viewer" : full ? "wide" : undefined}>
        <FullWidth.Provider value={fullWidth}>
          <Outlet />
        </FullWidth.Provider>
      </main>
      <SiteFooter />
    </>
  );
}


export function SiteFooter() {
  const { config } = useAuth();
  if (!config.organization && !config.contactEmail && !config.supportUrl) return null;
  return (
    <footer className="site-footer">
      {config.organization && <span>{config.organization}</span>}
      {config.contactEmail && <span>Help: <a href={`mailto:${config.contactEmail}`}>{config.contactEmail}</a></span>}
      {config.supportUrl && <span><a href={config.supportUrl}>Support</a></span>}
    </footer>
  );
}
