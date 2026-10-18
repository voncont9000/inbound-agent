import { NavLink, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import shell from "./components/AppShell.module.css";
import { RequireAuth } from "./auth/RequireAuth";
import { useAuth } from "./auth/AuthProvider";
import { LoginPage, SignupPage } from "./pages/AuthPages";
import { AssistantsPage, AssistantKnowledgePage, AssistantSettingsPage } from "./pages/AssistantPages";
import { ContactsPage, ContactPage, ConversationPage, InboxPage, KnowledgeGapsPage, TasksPage } from "./pages/OperatorPages";

function AppShell() {
  const { user, memberships, selectedTenant, setSelectedTenant, logout } = useAuth();
  const location = useLocation();
  const assistantArea = location.pathname.startsWith("/assistants/");
  const assistantId = location.pathname.split("/")[2];
  return (
    <div className={shell.frame}>
      <aside className={shell.sidebar}>
        <a className={shell.brand} href="/inbox">Inbound operator</a>
        <nav className={shell.navigation} aria-label="Main navigation">
          <NavLink to="/inbox">Inbox</NavLink>
          <NavLink to="/contacts">Contacts</NavLink>
          <NavLink to="/tasks">Tasks</NavLink>
          <NavLink to="/knowledge-gaps">Knowledge gaps</NavLink>
          <NavLink to="/assistants">Assistants</NavLink>
        </nav>
      </aside>
      <div className={shell.mainArea}>
        <header className={shell.topbar}>
          {memberships.length > 1 && <label>Workspace
            <select aria-label="Workspace" value={selectedTenant} onChange={(event) => setSelectedTenant(event.target.value)}>
              {memberships.map((membership) => <option key={membership.id} value={membership.id}>{membership.name}</option>)}
            </select>
          </label>}
          <span>{user?.display_name}</span>
          <button type="button" onClick={() => void logout()}>Log out</button>
        </header>
        {assistantArea && <nav className="subnav page" aria-label="Assistant sections">
          <NavLink to={`/assistants/${assistantId}/settings`}>Settings</NavLink>
          <NavLink to={`/assistants/${assistantId}/knowledge`}>Knowledge</NavLink>
        </nav>}
        <Outlet />
      </div>
    </div>
  );
}

export default function App() {
  return (
    <>
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<AppShell />}>
            <Route path="/" element={<Navigate to="/inbox" replace />} />
            <Route path="/assistants" element={<AssistantsPage />} />
            <Route path="/assistants/:id/settings" element={<AssistantSettingsPage />} />
            <Route path="/assistants/:id/knowledge" element={<AssistantKnowledgePage />} />
            <Route path="/inbox" element={<InboxPage />} />
            <Route path="/conversations/:id" element={<ConversationPage />} />
            <Route path="/contacts" element={<ContactsPage />} />
            <Route path="/contacts/:id" element={<ContactPage />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/knowledge-gaps" element={<KnowledgeGapsPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
