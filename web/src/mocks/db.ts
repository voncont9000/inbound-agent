import { cloneFixtures, authUser, memberships, templates, type TenantFixture } from "./fixtures";
import type { Schemas } from "../api/client";
import { resetAdminMocks } from "./admin";

export interface MockDatabase {
  tenants: TenantFixture[];
  loggedIn: boolean;
  isPlatformAdmin: boolean;
  user: typeof authUser;
  memberships: typeof memberships;
  jobs: Record<string, { tenantId: string; assistantId: string; polls: number; job: Schemas["JobResponse"] }>;
  setupDraftRequests: Schemas["AssistantSetupDraftCreate"][];
  setupCompletions: Schemas["AssistantSetupComplete"][];
  channels: Record<string, Schemas["ChannelConnectionResponse"][]>;
  unassignedChannels: Record<string, Schemas["ChannelConnectionResponse"][]>;
  postActionSecrets: Record<string, Schemas["WebhookSecretSummary"][]>;
}

function browserSession(): boolean {
  if (typeof localStorage === "undefined") return false;
  return localStorage.getItem("inbound.mock.session") === "1";
}

function createDb(): MockDatabase {
  const tenants = cloneFixtures();
  const assistantId = tenants[0].assistants[0].id;
  const secondAssistantId = tenants[0].assistants[1].id;
  return {
    tenants,
    loggedIn: browserSession(),
    isPlatformAdmin: true,
    user: { ...authUser },
    memberships: JSON.parse(JSON.stringify(memberships)) as typeof memberships,
    jobs: {},
    setupDraftRequests: [],
    setupCompletions: [],
    channels: {
      [assistantId]: [{
        id: "40000000-0000-4000-8000-000000000001",
        assistant_id: assistantId,
        kind: "gmail",
        external_id: "assistant@example.test",
        status: "active",
        settings: { include_rules: [] },
        created_at: "2026-02-01T10:30:00Z",
        google_features: [],
      }],
      [secondAssistantId]: [{
        id: "40000000-0000-4000-8000-000000000002",
        assistant_id: secondAssistantId,
        kind: "phone",
        external_id: "+442079460001",
        status: "active",
        settings: {},
        created_at: "2026-02-02T10:30:00Z",
        google_features: [],
      }],
    },
    unassignedChannels: Object.fromEntries(tenants.map(({ tenant }) => [tenant.id, []])),
    postActionSecrets: {},
  };
}

export let mockDb = createDb();

export function resetMockDb(): void {
  mockDb = createDb();
  resetAdminMocks();
}

export function setMockSession(loggedIn: boolean): void {
  mockDb.loggedIn = loggedIn;
  if (typeof localStorage === "undefined") return;
  if (loggedIn) localStorage.setItem("inbound.mock.session", "1");
  else localStorage.removeItem("inbound.mock.session");
}

export function setMockPlatformAdmin(isPlatformAdmin: boolean): void {
  mockDb.isPlatformAdmin = isPlatformAdmin;
  mockDb.user = { ...mockDb.user, is_platform_admin: isPlatformAdmin };
}

export function selectedTenantId(request: Request): string {
  const requested = request.headers.get("X-Tenant-Id");
  return mockDb.memberships.some((membership) => membership.id === requested)
    ? requested!
    : mockDb.memberships[0].id;
}

export function selectedTenant(request: Request): TenantFixture {
  const tenantId = selectedTenantId(request);
  return mockDb.tenants.find((tenant) => tenant.tenant.id === tenantId) ?? mockDb.tenants[0];
}

export { templates };
