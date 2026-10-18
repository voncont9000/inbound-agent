import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthProvider";
import { ErrorState, LoadingState } from "../components/States";
import { ApiError } from "../api/request";

export function RequireAuth() {
  const auth = useAuth();
  const location = useLocation();
  if (auth.loading) return <main id="main-content"><LoadingState /></main>;
  if (!auth.user) {
    if (auth.error && !(auth.error instanceof ApiError && auth.error.status === 401)) {
      return <main id="main-content"><ErrorState message="Your session could not be checked. Please refresh and try again." /></main>;
    }
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }
  return <Outlet />;
}
