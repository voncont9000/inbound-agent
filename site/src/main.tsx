import "@fontsource/roboto/400.css";
import "@fontsource/roboto/500.css";
import "@fontsource/bricolage-grotesque/700.css";
import "@fontsource/bricolage-grotesque/800.css";
import { StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { App } from "./App";
import { matchRoute } from "./routes";
import "./styles/index.css";

hydrateRoot(
  document.getElementById("root")!,
  <StrictMode>
    <App route={matchRoute(location.pathname)} />
  </StrictMode>,
);
