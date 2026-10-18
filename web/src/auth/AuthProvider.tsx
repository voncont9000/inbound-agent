import { createContext, useContext, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import type { Schemas } from "../api/client";
import { request } from "../api/request";

type Membership = Pick<Schemas["TenantResponse"], "id" | "name" | "role">;
type AuthContextValue = {
  user: Schemas["UserResponse"] | undefined;
  tenant: Membership | undefined;
  memberships: Membership[];
  selectedTenant: string;
  setSelectedTenant: (id: string) => void;
  loading: boolean;
  error: unknown;
  login: (email: string, password: string) => Promise<void>;
  signup: (input: Schemas["SignupRequest"]) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function storedMemberships(): Membership[] {
  try {
    return JSON.parse(localStorage.getItem("inbound.memberships") ?? "[]") as Membership[];
  } catch {
    return [];
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [memberships, setMemberships] = useState<Membership[]>(storedMemberships);
  const [selectedTenant, setSelectedTenantState] = useState(
    () => localStorage.getItem("inbound.tenant") ?? storedMemberships()[0]?.id ?? "",
  );
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => request<Schemas["MeResponse"]>("/me"),
    retry: false,
  });
  const setSelectedTenant = (id: string) => {
    setSelectedTenantState(id);
    localStorage.setItem("inbound.tenant", id);
    void queryClient.resetQueries();
  };

  async function saveAuth(response: Schemas["AuthResponse"]) {
    const safeMemberships = response.tenants.map(({ id, name, role }) => ({ id, name, role }));
    setMemberships(safeMemberships);
    localStorage.setItem("inbound.memberships", JSON.stringify(safeMemberships));
    if (safeMemberships[0]) setSelectedTenant(safeMemberships[0].id);
    await queryClient.invalidateQueries({ queryKey: ["me"] });
  }
  async function login(email: string, password: string) {
    const response = await request<Schemas["AuthResponse"]>("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    await saveAuth(response);
  }
  async function signup(input: Schemas["SignupRequest"]) {
    const response = await request<Schemas["AuthResponse"]>("/auth/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
    await saveAuth(response);
  }
  async function logout() {
    try {
      await request("/auth/logout", { method: "POST" });
    } catch {
      localStorage.removeItem("inbound.mock.session");
    } finally {
      localStorage.removeItem("inbound.memberships");
      localStorage.removeItem("inbound.tenant");
      localStorage.removeItem("inbound.mock.session");
      setMemberships([]);
      await queryClient.clear();
      navigate("/login", { replace: true });
    }
  }
  const value: AuthContextValue = {
    user: me.data?.user,
    tenant: me.data?.tenant,
    error: me.error,
    memberships,
    selectedTenant,
    setSelectedTenant,
    loading: me.isLoading,
    login,
    signup,
    logout,
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used within AuthProvider");
  return auth;
}
