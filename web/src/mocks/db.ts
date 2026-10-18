import { cloneFixtures, authUser, memberships, templates, type TenantFixture } from "./fixtures";

export interface MockDatabase {
  tenants: TenantFixture[];
  loggedIn: boolean;
  user: typeof authUser;
  memberships: typeof memberships;
  jobs: Record<string, { tenantId: string; assistantId: string; polls: number; job: Record<string, unknown> }>;
}

function browserSession(): boolean {
  if (typeof localStorage === "undefined") return false;
  return localStorage.getItem("inbound.mock.session") === "1";
}

function createDb(): MockDatabase {
  return {
    tenants: cloneFixtures(),
    loggedIn: browserSession(),
    user: { ...authUser },
    memberships: JSON.parse(JSON.stringify(memberships)) as typeof memberships,
    jobs: {},
  };
}

export let mockDb = createDb();

export function resetMockDb(): void {
  mockDb = createDb();
}

export function setMockSession(loggedIn: boolean): void {
  mockDb.loggedIn = loggedIn;
  if (typeof localStorage === "undefined") return;
  if (loggedIn) localStorage.setItem("inbound.mock.session", "1");
  else localStorage.removeItem("inbound.mock.session");
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
