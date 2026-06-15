export const LOCK_SCHEMA_VERSION = 5;

export const DEPENDENCY_MODES = ["pinned", "latest", "manual"] as const;

export type DependencyMode = (typeof DEPENDENCY_MODES)[number];

export const ACTIVE_LOCK_NAME = "source_roots.lock.jsonc";
export const TEMPLATE_LOCK_NAME = "source_roots.lock.jsonc.in";
export const WORKSPACE_LOCK_NAME = ".freecm.workspace.lock";

export const LOCK_FIELDS = {
  schemaVersion: "schemaVersion",
  depsMode: "depsMode",
  depsManualPath: "depsManualPath",
  dependencies: "dependencies",
  remote: "remote",
  commit: "commit",
} as const;

export const LOCK_SCHEMA_CONTRACT = {
  schemaVersion: LOCK_SCHEMA_VERSION,
  modes: DEPENDENCY_MODES,
  activeLockFileName: ACTIVE_LOCK_NAME,
  templateLockFileName: TEMPLATE_LOCK_NAME,
  workspaceLockName: WORKSPACE_LOCK_NAME,
  fields: LOCK_FIELDS,
} as const;

export function dependencyMode(value: unknown): DependencyMode | undefined {
  return DEPENDENCY_MODES.find((mode) => mode === value);
}
