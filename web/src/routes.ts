export const routes = [
  { path: "/login", label: "Login" },
  { path: "/assistants/:id/settings", label: "Assistant settings" },
  { path: "/assistants/:id/knowledge", label: "Assistant knowledge" },
  { path: "/inbox", label: "Inbox" },
  { path: "/conversations/:id", label: "Conversation" },
  { path: "/contacts", label: "Contacts" },
  { path: "/contacts/:id", label: "Contact" },
  { path: "/tasks", label: "Tasks" },
  { path: "/knowledge-gaps", label: "Knowledge gaps" },
] as const;
