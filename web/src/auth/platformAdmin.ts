import type { Schemas } from "../api/client";

export function isPlatformAdmin(user: Schemas["UserResponse"] | undefined): boolean {
  return user?.is_platform_admin === true;
}
