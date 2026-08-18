import type { Role } from "./types.ts";

export const VIEW_CAPABILITIES = [
  "dashboard.view",
  "stories.view",
  "drafts.view",
  "sources.view",
  "schedule.view",
  "settings.view",
  "diagnostics.view",
  "exports.download",
] as const;

export const OWNER_CAPABILITIES = [
  ...VIEW_CAPABILITIES,
  "stories.review",
  "evidence.manage",
  "content.create",
  "drafts.manage",
  "sources.manage",
  "schedule.manage",
  "settings.manage",
  "purge.manage",
] as const;

export type Capability = (typeof OWNER_CAPABILITIES)[number];
export type AccessMode = "view_only" | "full_control";

export type AccessProfile = {
  role: Role;
  mode: AccessMode;
  label: string;
  description: string;
  capabilities: readonly Capability[];
};

const PROFILES: Record<Role, AccessProfile> = {
  editor: {
    role: "editor",
    mode: "view_only",
    label: "View only",
    description:
      "Can view all hosted dashboard information and exports, but cannot change operational state.",
    capabilities: VIEW_CAPABILITIES,
  },
  master: {
    role: "master",
    mode: "full_control",
    label: "Owner · Full access",
    description:
      "Can view the complete dashboard and perform every supported operational action.",
    capabilities: OWNER_CAPABILITIES,
  },
};

export function accessProfile(role: Role): AccessProfile {
  return PROFILES[role];
}

export function can(role: Role, capability: Capability): boolean {
  return PROFILES[role].capabilities.includes(capability);
}

export function canMutate(role: Role): boolean {
  return role === "master";
}
