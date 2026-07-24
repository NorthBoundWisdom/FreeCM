import * as fs from "fs/promises";
import * as path from "path";
import { parse, ParseError, printParseErrorCode } from "jsonc-parser";
import { beginFilesystemRead } from "./performanceMetrics";

export const REPO_COMMAND_MANIFEST_PATH = path.join(
  "configs",
  "freecm.commands.jsonc",
);

export type RepoCommandAction = "config" | "build" | "run" | "test" | "package";
export type RepoCommandDependentAction = Exclude<RepoCommandAction, "config">;

export interface RepoCommandStep {
  readonly command: string;
  readonly args: readonly string[];
}

export interface RepoCommandReadinessSpec {
  readonly inputs: readonly string[];
  readonly outputs: readonly string[];
}

export interface RepoCommandVariant {
  readonly id: string;
  readonly label: string;
  readonly command?: string;
  readonly args?: readonly string[];
  readonly steps: readonly RepoCommandStep[];
  readonly description?: string;
  readonly platforms?: readonly string[];
  readonly default?: boolean;
  readonly defaults?: Readonly<
    Partial<Record<RepoCommandDependentAction, string>>
  >;
  readonly configurations?: readonly string[];
  readonly readiness?: RepoCommandReadinessSpec;
}

export interface RepoCommandActionState {
  readonly action: RepoCommandAction;
  readonly variants: readonly RepoCommandVariant[];
}

export interface RepoCommandManifestState {
  readonly manifestPath: string;
  readonly actions: Record<RepoCommandAction, RepoCommandActionState>;
  readonly configurations: readonly RepoCommandVariant[];
  readonly defaultConfiguration: RepoCommandVariant | undefined;
}

export interface RepoCommandWarning {
  readonly action: RepoCommandAction;
  readonly variantId: string;
  readonly variantLabel: string;
  readonly stepIndex: number;
  readonly message: string;
}

export const REPO_COMMAND_ACTIONS = [
  "config",
  "build",
  "run",
  "test",
  "package",
] as const;
export const REPO_COMMAND_DEPENDENT_ACTIONS = [
  "build",
  "run",
  "test",
  "package",
] as const satisfies readonly RepoCommandDependentAction[];
export const SUPPORTED_REPO_COMMAND_PLATFORMS = [
  "darwin",
  "linux",
  "win32",
] as const;

const SUPPORTED_VERSION = 2;

export async function loadRepoCommandManifest(
  repoRoot: string,
  platform: string = process.platform,
): Promise<RepoCommandManifestState | undefined> {
  const manifestPath = path.join(repoRoot, REPO_COMMAND_MANIFEST_PATH);
  let text: string;
  const finishRead = beginFilesystemRead();
  try {
    text = await fs.readFile(manifestPath, "utf8");
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) {
      return undefined;
    }
    throw new Error(`Unable to read ${manifestPath}: ${errorMessage(error)}`);
  } finally {
    finishRead();
  }

  return parseRepoCommandManifest(text, manifestPath, platform);
}

export function parseRepoCommandManifest(
  text: string,
  manifestPath: string,
  platform: string,
): RepoCommandManifestState {
  if (
    !SUPPORTED_REPO_COMMAND_PLATFORMS.includes(
      platform as (typeof SUPPORTED_REPO_COMMAND_PLATFORMS)[number],
    )
  ) {
    throw new Error(
      `Invalid command manifest platform ${JSON.stringify(
        platform,
      )}: expected darwin, linux, or win32`,
    );
  }

  const errors: ParseError[] = [];
  const value = parse(text, errors, { allowTrailingComma: true });
  if (errors.length > 0) {
    const details = errors
      .map(
        (error) =>
          `${printParseErrorCode(error.error)} at offset ${error.offset}`,
      )
      .join(", ");
    throw new Error(`Invalid JSONC in ${manifestPath}: ${details}`);
  }
  if (!isObject(value)) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: expected top-level object`,
    );
  }
  if (value.version !== SUPPORTED_VERSION) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: version must be ${SUPPORTED_VERSION}`,
    );
  }
  if (!isObject(value.commands)) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: commands must be an object`,
    );
  }
  const commands = value.commands;

  const parsedActions = Object.fromEntries(
    REPO_COMMAND_ACTIONS.map((action) => [
      action,
      parseAction(action, commands[action], manifestPath),
    ]),
  ) as Record<RepoCommandAction, RepoCommandActionState>;
  validateManifestRelationships(parsedActions, manifestPath);

  const configurations = parsedActions.config.variants.filter((variant) =>
    isPlatformCompatible(variant, platform),
  );
  const platformConfigurationIds = new Set(
    configurations.map((configuration) => configuration.id),
  );
  const platformAction = (
    action: RepoCommandDependentAction,
  ): RepoCommandActionState => ({
    action,
    variants: parsedActions[action].variants.filter((variant) =>
      variant.configurations?.some((configurationId) =>
        platformConfigurationIds.has(configurationId),
      ),
    ),
  });
  const actions: Record<RepoCommandAction, RepoCommandActionState> = {
    config: { action: "config", variants: configurations },
    build: platformAction("build"),
    run: platformAction("run"),
    test: platformAction("test"),
    package: platformAction("package"),
  };

  return {
    manifestPath,
    actions,
    configurations,
    defaultConfiguration: configurations.find(
      (configuration) => configuration.default === true,
    ),
  };
}

function parseAction(
  action: RepoCommandAction,
  value: unknown,
  manifestPath: string,
): RepoCommandActionState {
  if (value === undefined) {
    return { action, variants: [] };
  }
  if (!Array.isArray(value)) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: commands.${action} must be an array`,
    );
  }

  const variants = value.map((entry, index) =>
    parseVariant(action, entry, index, manifestPath),
  );
  assertUniqueVariantIds(action, variants, manifestPath);
  return { action, variants };
}

function parseVariant(
  action: RepoCommandAction,
  value: unknown,
  index: number,
  manifestPath: string,
): RepoCommandVariant {
  const prefix = `commands.${action}[${index}]`;
  if (!isObject(value)) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: ${prefix} must be an object`,
    );
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
    defaults?: Partial<Record<RepoCommandDependentAction, string>>;
    configurations?: string[];
    readiness?: RepoCommandReadinessSpec;
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
    variantWithOptionalString(
      value.description,
      manifestPath,
      `${prefix}.description`,
    );
    variant.description = value.description as string;
  }

  if (action === "config") {
    parseConfigVariantFields(value, variant, manifestPath, prefix);
  } else {
    parseDependentVariantFields(value, variant, manifestPath, prefix);
  }
  return variant;
}

function parseConfigVariantFields(
  value: Record<string, unknown>,
  variant: {
    platforms?: string[];
    default?: boolean;
    defaults?: Partial<Record<RepoCommandDependentAction, string>>;
    readiness?: RepoCommandReadinessSpec;
  },
  manifestPath: string,
  prefix: string,
): void {
  if (value.configurations !== undefined) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: ${prefix}.configurations is only valid for downstream actions`,
    );
  }

  if (value.platforms !== undefined) {
    const platforms = requiredNonEmptyStringArray(
      value.platforms,
      manifestPath,
      `${prefix}.platforms`,
    );
    for (const platform of platforms) {
      if (
        !SUPPORTED_REPO_COMMAND_PLATFORMS.includes(
          platform as (typeof SUPPORTED_REPO_COMMAND_PLATFORMS)[number],
        )
      ) {
        throw new Error(
          `Invalid command manifest ${manifestPath}: ${prefix}.platforms contains unsupported platform ${JSON.stringify(
            platform,
          )}`,
        );
      }
    }
    assertUniqueStrings(platforms, manifestPath, `${prefix}.platforms`);
    variant.platforms = platforms;
  }

  if (value.default !== undefined) {
    if (typeof value.default !== "boolean") {
      throw new Error(
        `Invalid command manifest ${manifestPath}: ${prefix}.default must be boolean`,
      );
    }
    variant.default = value.default;
  }

  variant.defaults = parseConfigurationDefaults(
    value.defaults,
    manifestPath,
    `${prefix}.defaults`,
  );
  if (value.readiness !== undefined) {
    variant.readiness = parseReadinessSpec(
      value.readiness,
      manifestPath,
      `${prefix}.readiness`,
    );
  }
}

function parseDependentVariantFields(
  value: Record<string, unknown>,
  variant: { configurations?: string[] },
  manifestPath: string,
  prefix: string,
): void {
  for (const configOnlyField of [
    "platforms",
    "default",
    "defaults",
    "readiness",
  ] as const) {
    if (value[configOnlyField] !== undefined) {
      throw new Error(
        `Invalid command manifest ${manifestPath}: ${prefix}.${configOnlyField} is only valid for Config variants`,
      );
    }
  }

  const configurations = requiredNonEmptyStringArray(
    value.configurations,
    manifestPath,
    `${prefix}.configurations`,
  );
  assertUniqueStrings(
    configurations,
    manifestPath,
    `${prefix}.configurations`,
  );
  variant.configurations = configurations;
}

function parseConfigurationDefaults(
  value: unknown,
  manifestPath: string,
  fieldPath: string,
): Partial<Record<RepoCommandDependentAction, string>> {
  if (!isObject(value)) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: ${fieldPath} must be an object`,
    );
  }

  const defaults: Partial<Record<RepoCommandDependentAction, string>> = {};
  for (const [action, variantId] of Object.entries(value)) {
    if (
      !REPO_COMMAND_DEPENDENT_ACTIONS.includes(
        action as RepoCommandDependentAction,
      )
    ) {
      throw new Error(
        `Invalid command manifest ${manifestPath}: ${fieldPath} contains unsupported action ${JSON.stringify(
          action,
        )}`,
      );
    }
    defaults[action as RepoCommandDependentAction] = requiredString(
      variantId,
      manifestPath,
      `${fieldPath}.${action}`,
    );
  }
  return defaults;
}

function parseReadinessSpec(
  value: unknown,
  manifestPath: string,
  fieldPath: string,
): RepoCommandReadinessSpec {
  if (!isObject(value)) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: ${fieldPath} must be an object`,
    );
  }

  const inputs = optionalStringArray(
    value.inputs,
    manifestPath,
    `${fieldPath}.inputs`,
  );
  const outputs = optionalStringArray(
    value.outputs,
    manifestPath,
    `${fieldPath}.outputs`,
  );
  assertUniqueStrings(inputs, manifestPath, `${fieldPath}.inputs`);
  assertUniqueStrings(outputs, manifestPath, `${fieldPath}.outputs`);
  for (const [kind, values] of [
    ["inputs", inputs],
    ["outputs", outputs],
  ] as const) {
    for (const repoPath of values) {
      assertRepoRelativePath(
        repoPath,
        manifestPath,
        `${fieldPath}.${kind}`,
      );
    }
  }
  return { inputs, outputs };
}

export function compatibleRepoCommandVariants(
  manifest: RepoCommandManifestState,
  configurationId: string,
  action: RepoCommandDependentAction,
): readonly RepoCommandVariant[] {
  if (
    !manifest.configurations.some(
      (configuration) => configuration.id === configurationId,
    )
  ) {
    return [];
  }
  return manifest.actions[action].variants.filter((variant) =>
    variant.configurations?.includes(configurationId),
  );
}

export function defaultRepoCommandVariant(
  manifest: RepoCommandManifestState,
  configurationId: string,
  action: RepoCommandDependentAction,
): RepoCommandVariant | undefined {
  const configuration = manifest.configurations.find(
    (candidate) => candidate.id === configurationId,
  );
  const defaultId = configuration?.defaults?.[action];
  if (defaultId === undefined) {
    return undefined;
  }
  return compatibleRepoCommandVariants(
    manifest,
    configurationId,
    action,
  ).find((variant) => variant.id === defaultId);
}

export function isRepoCommandVariantCompatible(
  variant: RepoCommandVariant,
  configurationId: string,
): boolean {
  return variant.configurations?.includes(configurationId) === true;
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

export function repoCommandWarnings(
  manifest: RepoCommandManifestState,
  platform: string = process.platform,
): RepoCommandWarning[] {
  return REPO_COMMAND_ACTIONS.flatMap((action) =>
    manifest.actions[action].variants.flatMap((variant) =>
      variant.steps.flatMap((step, index) =>
        repoCommandStepWarnings(action, variant, step, index, platform),
      ),
    ),
  );
}

function repoCommandStepWarnings(
  action: RepoCommandAction,
  variant: RepoCommandVariant,
  step: RepoCommandStep,
  stepIndex: number,
  platform: string,
): RepoCommandWarning[] {
  if (
    platform === "darwin" &&
    action === "run" &&
    step.command === "open" &&
    step.args.some((arg) => arg.endsWith(".app"))
  ) {
    return [
      {
        action,
        variantId: variant.id,
        variantLabel: variant.label,
        stepIndex,
        message:
          "macOS run command uses open on a .app bundle; this detaches from the terminal. Prefer .app/Contents/MacOS/<ExecutableName> so logs stay attached and Ctrl+C can stop the app.",
      },
    ];
  }
  return [];
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
    throw new Error(
      `Invalid command manifest ${manifestPath}: ${prefix} must be an object`,
    );
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

function validateManifestRelationships(
  actions: Record<RepoCommandAction, RepoCommandActionState>,
  manifestPath: string,
): void {
  const configurations = actions.config.variants;
  const configurationIds = new Set(
    configurations.map((configuration) => configuration.id),
  );

  for (const action of REPO_COMMAND_DEPENDENT_ACTIONS) {
    for (const variant of actions[action].variants) {
      for (const configurationId of variant.configurations ?? []) {
        if (!configurationIds.has(configurationId)) {
          throw new Error(
            `Invalid command manifest ${manifestPath}: commands.${action} variant ${JSON.stringify(
              variant.id,
            )} references unknown Config ${JSON.stringify(configurationId)}`,
          );
        }
      }
    }
  }

  for (const configuration of configurations) {
    for (const action of REPO_COMMAND_DEPENDENT_ACTIONS) {
      const variants = actions[action].variants.filter((variant) =>
        variant.configurations?.includes(configuration.id),
      );
      const defaultId = configuration.defaults?.[action];
      if (variants.length === 0) {
        if (defaultId !== undefined) {
          throw new Error(
            `Invalid command manifest ${manifestPath}: Config ${JSON.stringify(
              configuration.id,
            )} defaults.${action} references ${JSON.stringify(
              defaultId,
            )}, but no ${action} variants support that Config`,
          );
        }
        continue;
      }
      if (defaultId === undefined) {
        throw new Error(
          `Invalid command manifest ${manifestPath}: Config ${JSON.stringify(
            configuration.id,
          )} must declare defaults.${action} because compatible ${action} variants exist`,
        );
      }
      if (!variants.some((variant) => variant.id === defaultId)) {
        throw new Error(
          `Invalid command manifest ${manifestPath}: Config ${JSON.stringify(
            configuration.id,
          )} defaults.${action} ${JSON.stringify(
            defaultId,
          )} is not a compatible ${action} variant`,
        );
      }
    }
  }

  for (const platform of SUPPORTED_REPO_COMMAND_PLATFORMS) {
    const compatibleConfigurations = configurations.filter((configuration) =>
      isPlatformCompatible(configuration, platform),
    );
    if (compatibleConfigurations.length === 0) {
      continue;
    }
    const defaults = compatibleConfigurations.filter(
      (configuration) => configuration.default === true,
    );
    if (defaults.length !== 1) {
      throw new Error(
        `Invalid command manifest ${manifestPath}: platform ${platform} must have exactly one default Config; found ${defaults.length}`,
      );
    }
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
  if (
    !Array.isArray(value) ||
    !value.every((entry) => typeof entry === "string")
  ) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: ${fieldPath} must be a string array`,
    );
  }
  return [...value];
}

function requiredNonEmptyStringArray(
  value: unknown,
  manifestPath: string,
  fieldPath: string,
): string[] {
  const result = requiredStringArray(value, manifestPath, fieldPath);
  if (result.length === 0 || result.some((entry) => entry.trim() === "")) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: ${fieldPath} must be a non-empty string array`,
    );
  }
  return result;
}

function optionalStringArray(
  value: unknown,
  manifestPath: string,
  fieldPath: string,
): string[] {
  if (value === undefined) {
    return [];
  }
  const result = requiredStringArray(value, manifestPath, fieldPath);
  if (result.some((entry) => entry.trim() === "")) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: ${fieldPath} entries must be non-empty strings`,
    );
  }
  return result;
}

function assertUniqueStrings(
  values: readonly string[],
  manifestPath: string,
  fieldPath: string,
): void {
  const seen = new Set<string>();
  for (const value of values) {
    if (seen.has(value)) {
      throw new Error(
        `Invalid command manifest ${manifestPath}: ${fieldPath} contains duplicate value ${JSON.stringify(
          value,
        )}`,
      );
    }
    seen.add(value);
  }
}

function assertRepoRelativePath(
  value: string,
  manifestPath: string,
  fieldPath: string,
): void {
  const parts = value.split(/[\\/]/);
  if (
    path.isAbsolute(value) ||
    path.win32.isAbsolute(value) ||
    parts.includes("..")
  ) {
    throw new Error(
      `Invalid command manifest ${manifestPath}: ${fieldPath} path ${JSON.stringify(
        value,
      )} must stay within the repository`,
    );
  }
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

function isPlatformCompatible(
  variant: RepoCommandVariant,
  platform: string,
): boolean {
  return (
    variant.platforms === undefined || variant.platforms.includes(platform)
  );
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
