import type { ReactElement } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { Admin, ManageRedirect } from "./pages/admin";
import { Home } from "./pages/Home";
import { JobDetail } from "./pages/JobDetail";
import { Login } from "./pages/Login";
import { MySettings } from "./pages/MySettings";
import { Unsubscribe } from "./pages/Unsubscribe";
import { lazy, Suspense } from "react";

// The viewer bundles pdf.js, so it loads only when opened.
const TagViewer = lazy(() => import("./viewer/TagViewer").then((m) => ({ default: m.TagViewer })));

function RequireAuth({ children }: { children: ReactElement }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <main id="main"><Loading /></main>;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children;
}

function RequireAdmin({ children, superOnly = false }: { children: ReactElement; superOnly?: boolean }) {
  const { me } = useAuth();
  if (!me) return null;
  const ok = superOnly ? me.is_super_admin : me.is_super_admin || me.admin_of.length > 0;
  return ok ? children : <p>You don't have access to this page.</p>;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/unsubscribe" element={<Unsubscribe />} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<Home />} />
        <Route path="settings" element={<MySettings />} />
        <Route path="jobs/:id" element={<JobDetail />} />
        <Route path="jobs/:id/tags" element={<Suspense fallback={<Loading />}><TagViewer /></Suspense>} />
        <Route path="manage" element={<RequireAdmin><ManageRedirect /></RequireAdmin>} />
        <Route path="manage/:groupId/*" element={<RequireAdmin><ManageRedirect /></RequireAdmin>} />
        <Route path="admin/*" element={<RequireAdmin><Admin /></RequireAdmin>} />
        <Route path="*" element={<p>Page not found. <a href="/">Go to my documents</a>.</p>} />
      </Route>
    </Routes>
  );
}
