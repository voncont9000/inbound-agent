import { createContext, useContext, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";

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

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [selectedTenant, setSelectedTenantState] = useState(
    () => localStorage.getItem("inbound.tenant") ?? "",
  );
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => unwrap(api.GET("/me")),
    retry: false,
  });
  const setSelectedTenant = (id: string) => {
    setSelectedTenantState(id);
    localStorage.setItem("inbound.tenant", id);
    void queryClient.resetQueries();
  };

  async function saveAuth(response: Schemas["AuthResponse"]) {
    const membership = response.tenants[0];
    if (membership) setSelectedTenant(membership.id);
    await queryClient.invalidateQueries({ queryKey: ["me"] });
  }
  async function login(email: string, password: string) {
    const response = await unwrap(api.POST("/auth/login", { body: { email, password } }));
    await saveAuth(response);
  }
  async function signup(input: Schemas["SignupRequest"]) {
    const response = await unwrap(api.POST("/auth/signup", { body: input }));
    await saveAuth(response);
  }
  async function logout() {
    try {
      await unwrap(api.POST("/auth/logout"));
    } catch {
      localStorage.removeItem("inbound.mock.session");
    } finally {
      localStorage.removeItem("inbound.memberships");
      localStorage.removeItem("inbound.tenant");
      localStorage.removeItem("inbound.mock.session");
      await queryClient.clear();
      navigate("/login", { replace: true });
    }
  }
  const value: AuthContextValue = {
    user: me.data?.user,
    tenant: me.data?.tenant,
    error: me.error,
    memberships: me.data?.memberships ?? [],
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
