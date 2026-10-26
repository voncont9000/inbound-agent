import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./AuthProvider";
import { isPlatformAdmin } from "./platformAdmin";

export function RequireAdmin() {
  const { user } = useAuth();
  return isPlatformAdmin(user) ? <Outlet /> : <Navigate to="/" replace />;
}
