import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "../api/client";
import { ApiError } from "../api/errors";
import { useAuth } from "../auth/AuthProvider";
import { createBrowserWebCallSession, type WebCallSession, type WebCallSessionFactory } from "./webCall";

export type TestCallPhase = "idle" | "connecting" | "in_call" | "ended" | "error";

export type TestCallState = {
  phase: TestCallPhase;
  assistantId: string;
  pageUrl?: string;
  startedAt?: number;
  connectedAt?: number;
  endedAt?: number;
  error?: string;
  conversationId?: string | null;
  muted: boolean;
};

type TestCallContextValue = {
  state: TestCallState;
  assistants: { id: string; name: string }[];
  assistantsLoading: boolean;
  canTestCall: boolean;
  selectedAssistantId: string;
  selectAssistant: (id: string) => void;
  startCall: (assistantId?: string) => void;
  hangUp: () => void;
  toggleMute: () => void;
  dismiss: () => void;
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
};

const idleState: TestCallState = { phase: "idle", assistantId: "", muted: false };
const TestCallContext = createContext<TestCallContextValue | null>(null);
const COLLAPSED_KEY = "inbound.testcall.collapsed";
const assistantKey = (tenantId: string) => `inbound.testcall.assistant.${tenantId}`;

function apiDetail(error: unknown): string {
  if (error instanceof ApiError && error.body && typeof error.body === "object" && "detail" in error.body) {
    const detail = (error.body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return error instanceof Error ? error.message : "Unable to create a test call.";
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function TestCallProvider({ children, createSession = createBrowserWebCallSession }: { children: ReactNode; createSession?: WebCallSessionFactory }) {
  const { tenant, user, memberships, selectedTenant } = useAuth();
  const queryClient = useQueryClient();
  const tenantId = selectedTenant || tenant?.id || "";
  const role = memberships.find(({ id }) => id === tenantId)?.role ?? tenant?.role;
  const canTestCall = Boolean(user) && role === "owner";
  const assistantsQuery = useQuery({ queryKey: ["assistants"], queryFn: () => unwrap(api.GET("/assistants")), enabled: canTestCall });
  const assistants = useMemo(() => assistantsQuery.data?.items.map(({ id, name }) => ({ id, name })) ?? [], [assistantsQuery.data]);
  const [remembered, setRemembered] = useState("");
  const [state, setState] = useState<TestCallState>(idleState);
  const [collapsed, setCollapsedState] = useState(() => localStorage.getItem(COLLAPSED_KEY) === "1");
  const sessionRef = useRef<WebCallSession | null>(null);
  const callRef = useRef(0);

  useEffect(() => {
    setRemembered(tenantId ? localStorage.getItem(assistantKey(tenantId)) ?? "" : "");
  }, [tenantId]);

  const selectedAssistantId = assistants.some(({ id }) => id === remembered) ? remembered : assistants[0]?.id ?? "";
  const selectAssistant = useCallback((id: string) => {
    setRemembered(id);
    if (tenantId) localStorage.setItem(assistantKey(tenantId), id);
  }, [tenantId]);

  const setCollapsed = useCallback((value: boolean) => {
    setCollapsedState(value);
    localStorage.setItem(COLLAPSED_KEY, value ? "1" : "0");
  }, []);

  const findConversation = useCallback(async (assistantId: string, startedAt: number) => {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const page = await unwrap(api.GET("/conversations", { params: { query: { channel: "web", limit: 10 } } }));
        const match = page.items.find((item) => item.test_call && item.assistant_id === assistantId && new Date(item.started_at).getTime() >= startedAt - 60_000);
        if (match) return match.id;
      } catch { /* retry */ }
      await wait(1500);
    }
    return null;
  }, []);

  const startCall = useCallback((assistantId?: string) => {
    const target = assistantId || selectedAssistantId;
    if (!target || !canTestCall) return;
    if (sessionRef.current) sessionRef.current.hangUp();
    selectAssistant(target);
    const callId = ++callRef.current;
    const startedAt = Date.now();
    setState({ phase: "connecting", assistantId: target, startedAt, muted: false });
    setCollapsedState(false);
    void (async () => {
      let pageUrl = "";
      try {
        const response = await unwrap(api.POST("/assistants/{assistant_id}/test-calls", { params: { path: { assistant_id: target } } }));
        pageUrl = response.url;
      } catch (error) {
        if (callRef.current === callId) setState((current) => ({ ...current, phase: "error", error: apiDetail(error) }));
        return;
      }
      if (callRef.current !== callId) return;
      setState((current) => ({ ...current, pageUrl }));
      const session = createSession(pageUrl, {
        onConnected: () => {
          if (callRef.current === callId) setState((current) => ({ ...current, phase: "in_call", connectedAt: Date.now() }));
        },
        onEnded: (error) => {
          if (callRef.current !== callId) return;
          sessionRef.current = null;
          const endedAt = Date.now();
          setState((current) => ({ ...current, phase: error ? "error" : "ended", error, endedAt, conversationId: undefined }));
          void queryClient.invalidateQueries({ queryKey: ["conversations"] });
          void findConversation(target, startedAt).then((conversationId) => {
            if (callRef.current === callId) setState((current) => ({ ...current, conversationId }));
          });
        },
      });
      sessionRef.current = session;
      await session.start();
    })();
  }, [canTestCall, createSession, findConversation, queryClient, selectAssistant, selectedAssistantId]);

  const hangUp = useCallback(() => {
    const session = sessionRef.current;
    if (session) session.hangUp();
    else setState((current) => current.phase === "connecting" ? { ...current, phase: "ended", endedAt: Date.now() } : current);
  }, []);

  const toggleMute = useCallback(() => {
    setState((current) => {
      const muted = !current.muted;
      sessionRef.current?.setMuted(muted);
      return { ...current, muted };
    });
  }, []);

  const dismiss = useCallback(() => {
    callRef.current++;
    sessionRef.current?.hangUp();
    sessionRef.current = null;
    setState(idleState);
  }, []);

  useEffect(() => () => { sessionRef.current?.hangUp(); }, []);

  const value = useMemo<TestCallContextValue>(() => ({
    state, assistants, assistantsLoading: assistantsQuery.isLoading, canTestCall, selectedAssistantId, selectAssistant,
    startCall, hangUp, toggleMute, dismiss, collapsed, setCollapsed,
  }), [state, assistants, assistantsQuery.isLoading, canTestCall, selectedAssistantId, selectAssistant, startCall, hangUp, toggleMute, dismiss, collapsed, setCollapsed]);
  return <TestCallContext.Provider value={value}>{children}</TestCallContext.Provider>;
}

export function useTestCall(): TestCallContextValue {
  const context = useContext(TestCallContext);
  if (!context) throw new Error("useTestCall must be used within TestCallProvider");
  return context;
}

export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
