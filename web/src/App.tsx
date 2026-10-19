import { useEffect, useState } from "react";
import { Link, NavLink, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import shell from "./components/AppShell.module.css";
import { RequireAuth } from "./auth/RequireAuth";
import { useAuth } from "./auth/AuthProvider";
import { TestCallProvider } from "./call/TestCallProvider";
import type { WebCallSessionFactory } from "./call/webCall";
import { TestCallBar } from "./components/TestCallBar";
import { LoginPage, SignupPage } from "./pages/AuthPages";
import { AssistantSetupPage } from "./pages/AssistantSetupPage";
import { HomePage } from "./pages/HomePage";
import { AssistantsPage, AssistantSettingsPage, AssistantSettingsRedirect } from "./pages/AssistantPages";
import { ContactsPage, ContactPage, ConversationPage, InboxPage, KnowledgeGapsPage, TasksPage } from "./pages/OperatorPages";
import { HelpArticlePage, HelpHomePage } from "./help/HelpPages";

const icon = (path: string) => <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={path} /></svg>;

const NAV = [
  { to: "/", label: "Home", end: true, icon: icon("M3 11.5 12 4l9 7.5M5 10v10h5v-6h4v6h5V10") },
  { to: "/inbox", label: "Inbox", icon: icon("M22 12h-6l-2 3h-4l-2-3H2M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.7 4H7.3a2 2 0 0 0-1.8 1.1") },
  { to: "/contacts", label: "Contacts", icon: icon("M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8") },
  { to: "/tasks", label: "Tasks", icon: icon("M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11") },
  { to: "/knowledge-gaps", label: "Knowledge gaps", icon: icon("M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20") },
  { to: "/assistants", label: "Assistants", icon: icon("M12 2a3 3 0 0 1 3 3v1h2a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V9a3 3 0 0 1 3-3h2V5a3 3 0 0 1 3-3M9 13h.01M15 13h.01M9 17h6") },
  { to: "/help", label: "Help", icon: icon("M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20M12 6a6 6 0 1 0 0 12 6 6 0 0 0 0-12M4.9 4.9l2.9 2.9M16.2 16.2l2.9 2.9M4.9 19.1l2.9-2.9M16.2 7.8l2.9-2.9") },
];

function Brand() {
  return <Link className={shell.brand} to="/">
    <span className={shell.brandMark} aria-hidden="true">{icon("M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z")}</span>
    <span>Inbound operator</span>
  </Link>;
}

function AppShell() {
  const { user, tenant, memberships, selectedTenant, setSelectedTenant, logout } = useAuth();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const assistantArea = location.pathname.startsWith("/assistants/") && location.pathname !== "/assistants/new";
  const assistantId = location.pathname.split("/")[2];
  useEffect(() => { setMenuOpen(false); }, [location.pathname]);
  const initials = (user?.display_name ?? "?").split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
  const navigation = <nav className={shell.navigation} aria-label="Main navigation">
    {NAV.map(({ to, label, end, icon: glyph }) => <NavLink key={to} to={to} end={end}>{glyph}<span>{label}</span></NavLink>)}
  </nav>;

  return (
    <div className={shell.frame}>
      <aside className={shell.sidebar}>
        <Brand />
        {navigation}
        <div className={shell.sidebarFooter}>
          <span className={shell.workspaceLabel}>Workspace</span>
          <span className={shell.workspaceName}>{memberships.find(({ id }) => id === selectedTenant)?.name ?? tenant?.name}</span>
        </div>
      </aside>
      <div className={shell.mainArea}>
        <header className={shell.topbar}>
          <div className={shell.topbarLeft}>
            <button type="button" className={shell.menuButton} aria-expanded={menuOpen} aria-controls="mobile-navigation" onClick={() => setMenuOpen((open) => !open)}>
              {icon(menuOpen ? "M18 6 6 18M6 6l12 12" : "M4 7h16M4 12h16M4 17h16")}<span className="sr-only">{menuOpen ? "Close menu" : "Open menu"}</span>
            </button>
            <div className={shell.mobileBrand}><Brand /></div>
          </div>
          <div className={shell.topbarRight}>
            {memberships.length > 1
              ? <span className={shell.workspaceSelect}>
                <select aria-label="Workspace" value={selectedTenant} onChange={(event) => setSelectedTenant(event.target.value)}>
                  {memberships.map((membership) => <option key={membership.id} value={membership.id}>{membership.name}</option>)}
                </select>
              </span>
              : null}
            <span className={shell.user}><span className={shell.avatar} aria-hidden="true">{initials}</span><span className={shell.userName}>{user?.display_name}</span></span>
            <button type="button" className="button-ghost button-sm" onClick={() => void logout()}>Log out</button>
          </div>
        </header>
        <div id="mobile-navigation" className={shell.mobileNav} hidden={!menuOpen}>{navigation}</div>
        {assistantArea && <nav className="subnav page" aria-label="Assistant sections" style={{ paddingBlock: 0 }}>
          <NavLink to={`/assistants/${assistantId}/settings`}>Settings</NavLink>
        </nav>}
        <div className={shell.content}><Outlet /></div>
        <TestCallBar />
      </div>
    </div>
  );
}

export default function App({ createWebCallSession }: { createWebCallSession?: WebCallSessionFactory } = {}) {
  return (
    <>
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <TestCallProvider createSession={createWebCallSession}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<SignupPage />} />
          <Route element={<RequireAuth />}>
            <Route element={<AppShell />}>
              <Route path="/" element={<HomePage />} />
              <Route path="/assistants/new" element={<AssistantSetupPage />} />
              <Route path="/assistants" element={<AssistantsPage />} />
              <Route path="/assistants/:id/settings" element={<AssistantSettingsRedirect />} />
              <Route path="/assistants/:id/settings/:tab" element={<AssistantSettingsPage />} />
              <Route path="/assistants/:id/knowledge" element={<AssistantSettingsRedirect tab="knowledge" />} />
              <Route path="/inbox" element={<InboxPage />} />
              <Route path="/conversations/:id" element={<ConversationPage />} />
              <Route path="/contacts" element={<ContactsPage />} />
              <Route path="/contacts/:id" element={<ContactPage />} />
              <Route path="/tasks" element={<TasksPage />} />
              <Route path="/knowledge-gaps" element={<KnowledgeGapsPage />} />
              <Route path="/help" element={<HelpHomePage />} />
              <Route path="/help/:category/:article" element={<HelpArticlePage />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </TestCallProvider>
    </>
  );
}
