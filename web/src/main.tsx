import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import type { WebCallSessionFactory } from "./call/webCall";
import "./style.css";

async function start() {
  let createWebCallSession: WebCallSessionFactory | undefined;
  if (import.meta.env.VITE_USE_MOCKS === "1") {
    const { startMockWorker } = await import("./mocks/browser");
    await startMockWorker();
    ({ createFakeWebCallSession: createWebCallSession } = await import("./mocks/fakeWebCall"));
  }
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider><App createWebCallSession={createWebCallSession} /></AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </React.StrictMode>,
  );
}

void start();
