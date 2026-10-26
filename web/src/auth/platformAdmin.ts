import type { Schemas } from "../api/client";

export function isPlatformAdmin(user: Schemas["UserResponse"] | undefined): boolean {
  return (user as ({ is_platform_admin?: boolean } & Schemas["UserResponse"]) | undefined)
    ?.is_platform_admin === true;
}
