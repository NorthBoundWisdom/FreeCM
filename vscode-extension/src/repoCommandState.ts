import {
  RepoCommandAction,
  RepoCommandDependentAction,
  RepoCommandManifestState,
  RepoCommandVariant,
  compatibleRepoCommandVariants,
  defaultRepoCommandVariant,
} from "./repoCommands";

export const REPO_COMMAND_SELECTION_STATE_VERSION = 3;

export interface RepoCommandReadinessReceipt {
  readonly signature: string;
  readonly submittedAt: string;
}

export interface RepoCommandSelectionState {
  readonly version: 3;
  readonly activeConfigId?: string;
  readonly selectionsByConfig: Readonly<
    Record<
      string,
      Readonly<Partial<Record<RepoCommandDependentAction, string>>>
    >
  >;
  readonly readinessByConfig: Readonly<
    Record<string, RepoCommandReadinessReceipt>
  >;
}

export function emptyRepoCommandSelectionState(): RepoCommandSelectionState {
  return {
    version: REPO_COMMAND_SELECTION_STATE_VERSION,
    selectionsByConfig: {},
    readinessByConfig: {},
  };
}

export function repoCommandSelectionState(
  value: unknown,
): RepoCommandSelectionState {
  if (!isObject(value) || value.version !== REPO_COMMAND_SELECTION_STATE_VERSION) {
    return emptyRepoCommandSelectionState();
  }

  return {
    version: REPO_COMMAND_SELECTION_STATE_VERSION,
    activeConfigId:
      typeof value.activeConfigId === "string"
        ? value.activeConfigId
        : undefined,
    selectionsByConfig: parseSelectionsByConfig(value.selectionsByConfig),
    readinessByConfig: parseReadinessByConfig(value.readinessByConfig),
  };
}

export function repoCommandSelectionKey(folderFsPath: string): string {
  return `repoCommands.v3.${folderFsPath}`;
}

export function activeRepoCommandConfiguration(
  manifest: RepoCommandManifestState,
  state: RepoCommandSelectionState,
): RepoCommandVariant | undefined {
  return (
    manifest.configurations.find(
      (configuration) => configuration.id === state.activeConfigId,
    ) ?? manifest.defaultConfiguration
  );
}

export function repoCommandVariantsForSelection(
  manifest: RepoCommandManifestState,
  state: RepoCommandSelectionState,
  action: RepoCommandAction,
): readonly RepoCommandVariant[] {
  if (action === "config") {
    return manifest.configurations;
  }
  const configuration = activeRepoCommandConfiguration(manifest, state);
  if (configuration === undefined) {
    return [];
  }
  return compatibleRepoCommandVariants(manifest, configuration.id, action);
}

export function selectedRepoCommandVariant(
  manifest: RepoCommandManifestState,
  state: RepoCommandSelectionState,
  action: RepoCommandAction,
): RepoCommandVariant | undefined {
  const configuration = activeRepoCommandConfiguration(manifest, state);
  if (action === "config" || configuration === undefined) {
    return action === "config" ? configuration : undefined;
  }

  const variants = compatibleRepoCommandVariants(
    manifest,
    configuration.id,
    action,
  );
  const selectedId = state.selectionsByConfig[configuration.id]?.[action];
  return (
    variants.find((variant) => variant.id === selectedId) ??
    defaultRepoCommandVariant(manifest, configuration.id, action)
  );
}

export function withSelectedRepoCommandVariant(
  manifest: RepoCommandManifestState,
  state: RepoCommandSelectionState,
  action: RepoCommandAction,
  variantId: string,
): RepoCommandSelectionState {
  if (action === "config") {
    if (
      !manifest.configurations.some(
        (configuration) => configuration.id === variantId,
      )
    ) {
      throw new Error(`Unknown Config selection ${JSON.stringify(variantId)}`);
    }
    return {
      ...state,
      activeConfigId: variantId,
    };
  }

  const configuration = activeRepoCommandConfiguration(manifest, state);
  if (configuration === undefined) {
    throw new Error(`Select Config before selecting ${action}`);
  }
  const variants = compatibleRepoCommandVariants(
    manifest,
    configuration.id,
    action,
  );
  if (!variants.some((variant) => variant.id === variantId)) {
    throw new Error(
      `${action} variant ${JSON.stringify(
        variantId,
      )} is not compatible with Config ${JSON.stringify(configuration.id)}`,
    );
  }

  return {
    ...state,
    selectionsByConfig: {
      ...state.selectionsByConfig,
      [configuration.id]: {
        ...state.selectionsByConfig[configuration.id],
        [action]: variantId,
      },
    },
  };
}

export function withRepoCommandReadinessReceipt(
  state: RepoCommandSelectionState,
  configurationId: string,
  receipt: RepoCommandReadinessReceipt,
): RepoCommandSelectionState {
  return {
    ...state,
    readinessByConfig: {
      ...state.readinessByConfig,
      [configurationId]: receipt,
    },
  };
}

function parseSelectionsByConfig(
  value: unknown,
): RepoCommandSelectionState["selectionsByConfig"] {
  if (!isObject(value)) {
    return {};
  }
  const result: Record<
    string,
    Partial<Record<RepoCommandDependentAction, string>>
  > = {};
  for (const [configurationId, selections] of Object.entries(value)) {
    if (!isObject(selections)) {
      continue;
    }
    const parsed: Partial<Record<RepoCommandDependentAction, string>> = {};
    for (const action of ["build", "run", "test", "package"] as const) {
      if (typeof selections[action] === "string") {
        parsed[action] = selections[action];
      }
    }
    result[configurationId] = parsed;
  }
  return result;
}

function parseReadinessByConfig(
  value: unknown,
): RepoCommandSelectionState["readinessByConfig"] {
  if (!isObject(value)) {
    return {};
  }
  const result: Record<string, RepoCommandReadinessReceipt> = {};
  for (const [configurationId, receipt] of Object.entries(value)) {
    if (
      isObject(receipt) &&
      typeof receipt.signature === "string" &&
      typeof receipt.submittedAt === "string"
    ) {
      result[configurationId] = {
        signature: receipt.signature,
        submittedAt: receipt.submittedAt,
      };
    }
  }
  return result;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
