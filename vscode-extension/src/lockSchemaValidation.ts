import { parse, ParseError, printParseErrorCode } from "jsonc-parser";
import {
  DEPENDENCY_ENTRY_FIELDS,
  DependencyMode,
  LEGACY_DEPENDENCY_ENTRY_FIELDS,
  LOCK_FIELDS,
  LOCK_SCHEMA_VERSION,
  OPTIONAL_DEPENDENCY_ENTRY_FIELDS,
  REMOVED_TOP_LEVEL_FIELDS,
  REQUIRED_DEPENDENCY_ENTRY_FIELDS,
  dependencyMode,
  isSafeDependencyName,
} from "./lockSchema";

export interface DependencyEntry {
  readonly remote: string;
  readonly commit: string;
  readonly repoName?: string;
  readonly latestRef?: string;
}

export interface LockData {
  schemaVersion?: unknown;
  depsMode?: unknown;
  depsManualPath?: unknown;
  dependencies?: unknown;
}

export interface LockCoreProjection {
  readonly schemaVersion: number;
  readonly depsMode: DependencyMode;
  readonly depsManualPath: Readonly<Record<string, string>>;
  readonly dependencies: Readonly<Record<string, DependencyEntry>>;
}

export function parseLockText(text: string, filePath: string): LockData {
  const errors: ParseError[] = [];
  const value = parse(text, errors, { allowTrailingComma: true });
  if (errors.length > 0) {
    const details = errors
      .map(
        (error) =>
          `${printParseErrorCode(error.error)} at offset ${error.offset}`,
      )
      .join(", ");
    throw new Error(`Invalid JSONC in ${filePath}: ${details}`);
  }
  if (!isObject(value)) {
    throw new Error(`Invalid lock file ${filePath}: expected top-level object`);
  }
  validateMinimumLockShape(value, filePath);
  return value as LockData;
}

export function dependencyEntries(
  value: unknown,
  filePath: string,
): Record<string, DependencyEntry> {
  if (!isObject(value)) {
    throw new Error(`Invalid dependencies map in ${filePath}`);
  }

  const dependencies: Record<string, DependencyEntry> = {};
  for (const [name, entry] of Object.entries(value)) {
    if (!isSafeDependencyName(name)) {
      throw new Error(
        `Invalid dependency name ${JSON.stringify(name)} in ${filePath}`,
      );
    }
    if (!isObject(entry)) {
      throw new Error(`Invalid dependency entry for ${name} in ${filePath}`);
    }
    dependencies[name] = normalizeDependencyEntry(name, entry, filePath);
  }
  return dependencies;
}

export function lockCoreProjection(
  value: LockData,
  filePath: string,
): LockCoreProjection {
  if (!isObject(value)) {
    throw new Error(`Invalid lock file ${filePath}: expected top-level object`);
  }
  validateMinimumLockShape(value, filePath);
  const mode = dependencyMode(value[LOCK_FIELDS.depsMode]);
  if (mode === undefined) {
    throw new Error(`Invalid depsMode in ${filePath}`);
  }
  const manualPaths = value[LOCK_FIELDS.depsManualPath] as Record<string, string>;
  return {
    schemaVersion: LOCK_SCHEMA_VERSION,
    depsMode: mode,
    depsManualPath: Object.fromEntries(
      Object.entries(manualPaths).sort(([left], [right]) =>
        left.localeCompare(right),
      ),
    ),
    dependencies: Object.fromEntries(
      Object.entries(
        dependencyEntries(value[LOCK_FIELDS.dependencies], filePath),
      ).sort(([left], [right]) => left.localeCompare(right)),
    ),
  };
}

function validateMinimumLockShape(
  value: Record<string, unknown>,
  filePath: string,
): void {
  if (value[LOCK_FIELDS.schemaVersion] !== LOCK_SCHEMA_VERSION) {
    throw new Error(`Unsupported schemaVersion in ${filePath}`);
  }
  if (dependencyMode(value[LOCK_FIELDS.depsMode]) === undefined) {
    throw new Error(`Invalid depsMode in ${filePath}`);
  }
  for (const [removedField, replacement] of Object.entries(
    REMOVED_TOP_LEVEL_FIELDS,
  )) {
    if (Object.prototype.hasOwnProperty.call(value, removedField)) {
      throw new Error(
        `${removedField} is no longer supported in ${filePath}; use ${replacement}`,
      );
    }
  }
  const depsManualPath = value[LOCK_FIELDS.depsManualPath];
  const dependencies = value[LOCK_FIELDS.dependencies];
  if (!isObject(depsManualPath)) {
    throw new Error(`Invalid depsManualPath map in ${filePath}`);
  }
  if (!isObject(dependencies)) {
    throw new Error(`Invalid dependencies map in ${filePath}`);
  }
  assertMatchingDependencyKeys(dependencies, depsManualPath, filePath);
  for (const [name, manualPath] of Object.entries(depsManualPath)) {
    if (!isSafeDependencyName(name)) {
      throw new Error(
        `Invalid dependency name ${JSON.stringify(name)} in ${filePath}`,
      );
    }
    if (typeof manualPath !== "string") {
      throw new Error(`Invalid depsManualPath.${name} in ${filePath}`);
    }
  }
  for (const [name, entry] of Object.entries(dependencies)) {
    if (!isSafeDependencyName(name)) {
      throw new Error(
        `Invalid dependency name ${JSON.stringify(name)} in ${filePath}`,
      );
    }
    if (!isObject(entry)) {
      throw new Error(`Invalid dependency entry for ${name} in ${filePath}`);
    }
    normalizeDependencyEntry(name, entry, filePath);
  }
}

function assertMatchingDependencyKeys(
  dependencies: Record<string, unknown>,
  depsManualPath: Record<string, unknown>,
  filePath: string,
): void {
  const dependencyNames = Object.keys(dependencies).sort();
  const manualPathNames = Object.keys(depsManualPath).sort();
  if (
    dependencyNames.length !== manualPathNames.length ||
    dependencyNames.some((name, index) => name !== manualPathNames[index])
  ) {
    throw new Error(
      `Invalid depsManualPath in ${filePath}: keys must match dependencies`,
    );
  }
}

function normalizeDependencyEntry(
  dependencyName: string,
  entry: Record<string, unknown>,
  filePath: string,
): DependencyEntry {
  const allowedFields: ReadonlySet<string> = new Set([
    ...DEPENDENCY_ENTRY_FIELDS,
    ...LEGACY_DEPENDENCY_ENTRY_FIELDS,
  ]);
  for (const key of Object.keys(entry)) {
    if (!allowedFields.has(key)) {
      throw new Error(
        `Invalid dependency ${dependencyName} in ${filePath}: unexpected field ${key}`,
      );
    }
  }

  const required = Object.fromEntries(
    REQUIRED_DEPENDENCY_ENTRY_FIELDS.map((field) => [
      field,
      requiredDependencyString(dependencyName, entry, field, filePath),
    ]),
  );
  const optional = Object.fromEntries(
    OPTIONAL_DEPENDENCY_ENTRY_FIELDS.map((field) => [
      field,
      optionalDependencyString(dependencyName, entry, field, filePath),
    ]),
  );
  const repoName = optional[LOCK_FIELDS.repoName];
  if (repoName !== undefined && !isSafeDependencyName(repoName)) {
    throw new Error(
      `Invalid field ${LOCK_FIELDS.repoName} for dependency ${dependencyName} in ${filePath}: expected safe repository name`,
    );
  }
  return {
    remote: required[LOCK_FIELDS.remote],
    commit: required[LOCK_FIELDS.commit],
    ...(repoName === undefined ? {} : { repoName }),
    ...(optional[LOCK_FIELDS.latestRef] === undefined
      ? {}
      : { latestRef: optional[LOCK_FIELDS.latestRef] }),
  };
}

function requiredDependencyString(
  dependencyName: string,
  entry: Record<string, unknown>,
  field: string,
  filePath: string,
): string {
  const value = entry[field];
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(
      `Invalid field ${field} for dependency ${dependencyName} in ${filePath}`,
    );
  }
  return value.trim();
}

function optionalDependencyString(
  dependencyName: string,
  entry: Record<string, unknown>,
  field: string,
  filePath: string,
): string | undefined {
  const value = entry[field];
  if (value === undefined || value === null) {
    return undefined;
  }
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(
      `Invalid field ${field} for dependency ${dependencyName} in ${filePath}: expected non-empty string`,
    );
  }
  return value.trim();
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
