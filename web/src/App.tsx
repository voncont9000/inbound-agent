import { Link, Route, Routes, useLocation } from "react-router-dom";
import { routes } from "./routes";

function RouteIndex() {
  return (
    <main>
      <p className="eyebrow">M1 foundation</p>
      <h1>Inbound workspace</h1>
      <p className="intro">Shared route contracts for the next platform agents.</p>
      <ul className="route-list">
        {routes.map((route) => (
          <li key={route.path}>
            <Link to={route.path}>
              <span>{route.label}</span>
              <code>{route.path}</code>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}

function Placeholder() {
  const location = useLocation();
  return (
    <main>
      <p className="eyebrow">M1 foundation</p>
      <h1>Page reserved for the web app agent</h1>
      <p className="intro">
        <code>{location.pathname}</code>
      </p>
      <Link to="/">View all planned routes</Link>
    </main>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<RouteIndex />} />
      {routes.map((route) => (
        <Route key={route.path} path={route.path} element={<Placeholder />} />
      ))}
      <Route path="*" element={<RouteIndex />} />
    </Routes>
  );
}
