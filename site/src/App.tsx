import type { RouteDef } from "./routes";

export function App({ route }: { route: RouteDef }) {
  const C = route.Component;
  return <C />;
}
