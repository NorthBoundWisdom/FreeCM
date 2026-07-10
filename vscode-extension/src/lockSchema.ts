export const LOCK_SCHEMA_VERSION = 5;

export const DEPENDENCY_MODES = ["pinned", "latest", "manual"] as const;

export type DependencyMode = (typeof DEPENDENCY_MODES)[number];

export const ACTIVE_LOCK_NAME = "source_roots.lock.jsonc";
export const TEMPLATE_LOCK_NAME = "source_roots.lock.jsonc.in";
export const WORKSPACE_LOCK_NAME = ".freecm.workspace.lock";
export const WORKSPACE_LOCK_PROTOCOL = {
  schemaVersion: 1,
  ownerFileName: "owner.json",
  timeoutMs: 5000,
  retryDelayMs: 50,
  initializationGraceMs: 2000,
} as const;
export const LEGACY_DEPENDENCY_ENTRY_FIELDS = ["abiGroup"] as const;

export const LOCK_FIELDS = {
  schemaVersion: "schemaVersion",
  depsMode: "depsMode",
  depsManualPath: "depsManualPath",
  dependencies: "dependencies",
  repoName: "repoName",
  remote: "remote",
  commit: "commit",
  latestRef: "latestRef",
} as const;

export const LOCK_SCHEMA_CONTRACT = {
  schemaVersion: LOCK_SCHEMA_VERSION,
  modes: DEPENDENCY_MODES,
  activeLockFileName: ACTIVE_LOCK_NAME,
  templateLockFileName: TEMPLATE_LOCK_NAME,
  workspaceLockName: WORKSPACE_LOCK_NAME,
  workspaceLockProtocol: WORKSPACE_LOCK_PROTOCOL,
  legacyDependencyEntryFields: LEGACY_DEPENDENCY_ENTRY_FIELDS,
  fields: LOCK_FIELDS,
} as const;

export function dependencyMode(value: unknown): DependencyMode | undefined {
  return DEPENDENCY_MODES.find((mode) => mode === value);
}
