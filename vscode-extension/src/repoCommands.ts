import * as fs from "fs/promises";
import * as path from "path";
import { parse, ParseError, printParseErrorCode } from "jsonc-parser";

export const REPO_COMMAND_MANIFEST_PATH = path.join(
  "configs",
  "freecm.commands.jsonc",
);

export type RepoCommandAction = "config" | "build" | "test" | "run";

export interface RepoCommandVariant {
  readonly id: string;
  readonly label: string;
  readonly command?: string;
  readonly args?: readonly string[];
  readonly steps: readonly RepoCommandStep[];
  readonly description?: string;
  readonly platforms?: readonly string[];
  readonly default?: boolean;
}

export interface RepoCommandStep {
  readonly command: string;
  readonly args: readonly string[];
}

export interface RepoCommandActionState {
  readonly action: RepoCommandAction;
  readonly variants: readonly RepoCommandVariant[];
  readonly defaultVariant: RepoCommandVariant | undefined;
}

export interface RepoCommandManifestState {
  readonly manifestPath: string;
  readonly actions: Record<RepoCommandAction, RepoCommandActionState>;
}

export const REPO_COMMAND_ACTIONS = ["config", "build", "run", "test"] as const;
const SUPPORTED_PLATFORMS = ["darwin", "linux", "win32"] as const;
const SUPPORTED_VERSION = 1;

export async function loadRepoCommandManifest(
  repoRoot: string,
  platform: string = process.platform,
): Promise<RepoCommandManifestState | undefined> {
  const manifestPath = path.join(repoRoot, REPO_COMMAND_MANIFEST_PATH);
  let text: string;
  try {
    text = await fs.readFile(manifestPath, "utf8");
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) {
      return undefined;
    }
    throw new Error(`Unable to read ${manifestPath}: ${errorMessage(error)}`);
  }

  return parseRepoCommandManifest(text, manifestPath, platform);
}

export function parseRepoCommandManifest(
  text: string,
  manifestPath: string,
  platform: string,
): RepoCommandManifestState {
  const errors: ParseError[] = [];
  const value = parse(text, errors, { allowTrailingComma: true });
  if (errors.length > 0) {
    const details = errors
      .map((error) => `${printParseErrorCode(error.error)} at offset ${error.offset}`)
      .join(", ");
    throw new Error(`Invalid JSONC in ${manifestPath}: ${details}`);
  }
  if (!isObject(value)) {
    throw new Error(`Invalid command manifest ${manifestPath}: expected top-level object`);
  }
  if (value.version !== SUPPORTED_VERSION) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: version must be ${SUPPORTED_VERSION}`,
    );
  }
  if (!isObject(value.commands)) {
    throw new Error(`Invalid command manifest ${manifestPath}: commands must be an object`);
  }
  const commands = value.commands;

  const actions = Object.fromEntries(
    REPO_COMMAND_ACTIONS.map((action) => [
      action,
      parseAction(action, commands[action], manifestPath, platform),
    ]),
  ) as Record<RepoCommandAction, RepoCommandActionState>;

  return { manifestPath, actions };
}

function parseAction(
  action: RepoCommandAction,
  value: unknown,
  manifestPath: string,
  platform: string,
): RepoCommandActionState {
  if (value === undefined) {
    return { action, variants: [], defaultVariant: undefined };
  }
  if (!Array.isArray(value)) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: commands.${action} must be an array`,
    );
  }

  const parsedVariants = value.map((entry, index) =>
    parseVariant(action, entry, index, manifestPath),
  );
  assertUniqueVariantIds(action, parsedVariants, manifestPath);
  const variants = parsedVariants.filter((variant) =>
    isPlatformCompatible(variant, platform),
  );
  const defaultVariant =
    variants.find((variant) => variant.default === true) ?? variants[0];
  return { action, variants, defaultVariant };
}

function parseVariant(
  action: RepoCommandAction,
  value: unknown,
  index: number,
  manifestPath: string,
): RepoCommandVariant {
  const prefix = `commands.${action}[${index}]`;
  if (!isObject(value)) {
    throw new Error(`Invalid command manifest ${manifestPath}: ${prefix} must be an object`);
  }

  const id = requiredString(value.id, manifestPath, `${prefix}.id`);
  const label = requiredString(value.label, manifestPath, `${prefix}.label`);
  const steps = parseVariantSteps(value, manifestPath, prefix);

  const variant: {
    id: string;
    label: string;
    command?: string;
    args?: string[];
    steps: RepoCommandStep[];
    description?: string;
    platforms?: string[];
    default?: boolean;
  } = {
    id,
    label,
    steps,
  };

  if (value.steps === undefined) {
    variant.command = steps[0].command;
    variant.args = [...steps[0].args];
  }

  if (value.description !== undefined) {
    variantWithOptionalString(value.description, manifestPath, `${prefix}.description`);
    variant.description = value.description as string;
  }
  if (value.platforms !== undefined) {
    if (
      !Array.isArray(value.platforms) ||
      !value.platforms.every((platform) => typeof platform === "string")
    ) {
      throw new Error(
        `Invalid command manifest ${manifestPath}: ${prefix}.platforms must be a string array`,
      );
    }
    for (const platform of value.platforms) {
      if (!SUPPORTED_PLATFORMS.includes(platform as (typeof SUPPORTED_PLATFORMS)[number])) {
        throw new Error(
          `Invalid command manifest ${manifestPath}: ${prefix}.platforms contains unsupported platform ${JSON.stringify(
            platform,
          )}`,
        );
      }
    }
    variant.platforms = [...value.platforms];
  }
  if (value.default !== undefined) {
    if (typeof value.default !== "boolean") {
      throw new Error(
        `Invalid command manifest ${manifestPath}: ${prefix}.default must be boolean`,
      );
    }
    variant.default = value.default;
  }

  return variant;
}

export function commandLineForTerminal(
  variant: RepoCommandVariant,
  platform: string = process.platform,
): string {
  return commandLinesForTerminal(variant, platform).join("\n");
}

export function commandLinesForTerminal(
  variant: RepoCommandVariant,
  platform: string = process.platform,
): string[] {
  const quote = platform === "win32" ? windowsShellQuote : posixShellQuote;
  return variant.steps.map((step) =>
    [step.command, ...step.args].map(quote).join(" "),
  );
}

function parseVariantSteps(
  value: Record<string, unknown>,
  manifestPath: string,
  prefix: string,
): RepoCommandStep[] {
  if (value.steps !== undefined) {
    if (value.command !== undefined || value.args !== undefined) {
      throw new Error(
        `Invalid command manifest ${manifestPath}: ${prefix} must use either command/args or steps, not both`,
      );
    }
    if (!Array.isArray(value.steps) || value.steps.length === 0) {
      throw new Error(
        `Invalid command manifest ${manifestPath}: ${prefix}.steps must be a non-empty array`,
      );
    }
    return value.steps.map((step, index) =>
      parseCommandStep(step, manifestPath, `${prefix}.steps[${index}]`),
    );
  }

  return [
    {
      command: requiredString(value.command, manifestPath, `${prefix}.command`),
      args: requiredStringArray(value.args, manifestPath, `${prefix}.args`),
    },
  ];
}

function parseCommandStep(
  value: unknown,
  manifestPath: string,
  prefix: string,
): RepoCommandStep {
  if (!isObject(value)) {
    throw new Error(`Invalid command manifest ${manifestPath}: ${prefix} must be an object`);
  }
  return {
    command: requiredString(value.command, manifestPath, `${prefix}.command`),
    args: requiredStringArray(value.args, manifestPath, `${prefix}.args`),
  };
}

function posixShellQuote(value: string): string {
  if (value.length > 0 && /^[A-Za-z0-9_./:=@%+-]+$/.test(value)) {
    return value;
  }
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

function windowsShellQuote(value: string): string {
  if (value.length > 0 && /^[A-Za-z0-9_./:=@%+\\-]+$/.test(value)) {
    return value;
  }
  return `"${value.replace(/"/g, '\\"')}"`;
}

function assertUniqueVariantIds(
  action: RepoCommandAction,
  variants: readonly RepoCommandVariant[],
  manifestPath: string,
): void {
  const seen = new Set<string>();
  for (const variant of variants) {
    if (seen.has(variant.id)) {
      throw new Error(
        `Invalid command manifest ${manifestPath}: commands.${action} contains duplicate id ${JSON.stringify(
          variant.id,
        )}`,
      );
    }
    seen.add(variant.id);
  }
}

function requiredString(
  value: unknown,
  manifestPath: string,
  fieldPath: string,
): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(
      `Invalid command manifest ${manifestPath}: ${fieldPath} must be a non-empty string`,
    );
  }
  return value;
}

function requiredStringArray(
  value: unknown,
  manifestPath: string,
  fieldPath: string,
): string[] {
  if (!Array.isArray(value) || !value.every((entry) => typeof entry === "string")) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: ${fieldPath} must be a string array`,
    );
  }
  return [...value];
}

function variantWithOptionalString(
  value: unknown,
  manifestPath: string,
  fieldPath: string,
): void {
  if (typeof value !== "string") {
    throw new Error(
      `Invalid command manifest ${manifestPath}: ${fieldPath} must be a string`,
    );
  }
}

function isPlatformCompatible(variant: RepoCommandVariant, platform: string): boolean {
  return variant.platforms === undefined || variant.platforms.includes(platform);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNodeErrorCode(error: unknown, code: string): boolean {
  return isObject(error) && error.code === code;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
