import type { JSX } from "react";
import Home from "./pages/Home";
import Imprint from "./pages/Imprint";
import NotFound from "./pages/NotFound";
import Privacy from "./pages/Privacy";

export type RouteMeta = { path: string; title: string; description: string; noindex?: boolean };
export type RouteDef = RouteMeta & { Component: () => JSX.Element };

export const routes: RouteDef[] = [
  {
    path: "/",
    title: "Yelvo · AI receptionist for calls, Gmail and WhatsApp",
    description:
      "Yelvo answers every call, email and WhatsApp message for your business, collects the details and hands your team the follow-up.",
    Component: Home,
  },
  {
    path: "/privacy",
    title: "Privacy policy · Yelvo",
    description: "Learn how Yelvo handles personal information.",
    noindex: true,
    Component: Privacy,
  },
  {
    path: "/imprint",
    title: "Imprint · Yelvo",
    description: "Legal information for Yelvo.",
    noindex: true,
    Component: Imprint,
  },
  {
    path: "/404",
    title: "Page not found · Yelvo",
    description: "The page you are looking for could not be found.",
    noindex: true,
    Component: NotFound,
  },
];

export function matchRoute(pathname: string): RouteDef {
  const normalizedPath = pathname.replace(/\/index\.html$/, "").replace(/\/+$/, "") || "/";
  return routes.find((route) => route.path === normalizedPath) ?? routes[3];
}
