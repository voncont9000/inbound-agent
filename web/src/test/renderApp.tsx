import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import App from "../App";
import { AuthProvider } from "../auth/AuthProvider";
import { setMockSession } from "../mocks/db";

function LocationProbe() {
  const location = useLocation();
  return <output className="sr-only" data-testid="current-location">{location.pathname}{location.search}</output>;
}

export function renderApp(path = "/", options: { loggedIn?: boolean } = {}) {
  localStorage.clear();
  setMockSession(options.loggedIn ?? false);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[path]}>
          <AuthProvider><App /><LocationProbe /></AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}
