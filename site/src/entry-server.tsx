import { StrictMode } from "react";
import { renderToString } from "react-dom/server";
import { App } from "./App";
import { renderHead } from "./head";
import { matchRoute } from "./routes";

export function render(path: string) {
  const route = matchRoute(path);
  return {
    html: renderToString(
      <StrictMode>
        <App route={route} />
      </StrictMode>,
    ),
    head: renderHead(route),
  };
}
