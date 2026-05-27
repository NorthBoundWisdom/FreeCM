import { randomBytes } from "crypto";
import { DependencyComparison } from "../lockWorkflow";
import {
  REPO_COMMAND_ACTIONS,
  RepoCommandAction,
  RepoCommandVariant,
} from "../repoCommands";
import { RepoWorkspaceFolder } from "../workspaceDiscovery";
import { EXTENSION_BUILD_INFO } from "../buildInfo";
import { RepoCommandSelectCommand } from "./messageProtocol";

export interface WorkflowViewState {
  readonly eligibleFolders: readonly RepoWorkspaceFolder[];
  readonly targetName: string | undefined;
  readonly launching: boolean;
  readonly lockMode: string | undefined;
  readonly lockStatusUnavailable: boolean;
  readonly dependencyComparison: DependencyComparisonViewState;
  readonly repoCommands: RepoCommandViewState;
  readonly codeCount: CodeCountViewState;
}

export interface CodeCountViewState {
  readonly enabled: boolean;
  readonly targetPath: string | undefined;
  readonly targetLabel: string | undefined;
  readonly outputLabel: string | undefined;
}

export interface DependencyComparisonViewState {
  readonly status: "ready" | "empty" | "unavailable";
  readonly sampleMode: string | undefined;
  readonly activeMode: string | undefined;
  readonly rows: readonly DependencyComparisonRowViewState[];
}

export interface DependencyComparisonRowViewState {
  readonly name: string;
  readonly samplePresent: boolean;
  readonly sampleCommit: string | undefined;
  readonly activePresent: boolean;
  readonly activeCommit: string | undefined;
  readonly activeMode: string | undefined;
}

export interface RepoCommandViewState {
  readonly status: "missing" | "ready" | "error";
  readonly message: string | undefined;
  readonly actions: Record<RepoCommandAction, RepoCommandActionViewState>;
}

export interface RepoCommandActionViewState {
  readonly action: RepoCommandAction;
  readonly enabled: boolean;
  readonly selectedLabel: string | undefined;
  readonly variantCount: number;
}

interface WorkflowViewHtmlResources {
  readonly nonce?: string;
  readonly cspSource?: string;
  readonly scriptUri?: string;
  readonly styleUri?: string;
}

export function workflowViewHtml(
  state: WorkflowViewState,
  resources: WorkflowViewHtmlResources | string = {},
): string {
  const nonce = typeof resources === "string" ? resources : resources.nonce ?? webviewNonce();
  const cspSource = typeof resources === "string" ? "" : resources.cspSource ?? "";
  const styleSource = cspSource === "" ? "'none'" : escapeHtml(cspSource);
  const scriptUri = typeof resources === "string" ? undefined : resources.scriptUri;
  const styleUri = typeof resources === "string" ? undefined : resources.styleUri;
  const hasEligibleWorkspace = state.eligibleFolders.length > 0;
  const targetLabel =
    state.targetName === undefined
      ? hasEligibleWorkspace
        ? "Multiple workspaces"
        : "No workspace"
      : escapeHtml(state.targetName);
  const disabled = !hasEligibleWorkspace || state.launching ? "disabled" : "";
  const statusClass = hasEligibleWorkspace ? "ready" : "empty";
  const buildInfoText = `${escapeHtml(EXTENSION_BUILD_INFO.version)} · ${escapeHtml(
    EXTENSION_BUILD_INFO.compiledAt,
  )}`;
  const repoCommandMessage =
    state.repoCommands.status === "ready"
      ? ""
      : state.repoCommands.message === undefined
        ? ""
        : escapeHtml(state.repoCommands.message);
  const repoCommandStatusClass =
    state.repoCommands.status === "error" ? "command-status error" : "command-status";
  const dependencyComparisonHtml = dependencyComparisonSectionHtml(
    state.dependencyComparison,
  );
  const codeCountHtml = codeCountSectionHtml(state.codeCount, disabled);
  const commandRows = REPO_COMMAND_ACTIONS.map((action) =>
    repoCommandRowHtml(state.repoCommands.actions[action], disabled),
  ).join("");

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${styleSource}; script-src 'nonce-${nonce}' ${escapeHtml(cspSource)};">
  ${styleUri === undefined ? "" : `<link rel="stylesheet" href="${escapeHtml(styleUri)}">`}
</head>
<body>
  <main class="panel">
    <section class="target-card ${statusClass}">
      <div class="build-info">${buildInfoText}</div>
      <div class="target-name" title="${targetLabel}">${targetLabel}</div>
    </section>

    <section class="section" aria-labelledby="workflow-title">
      <div class="section-header">
        <div id="workflow-title" class="section-title">Workflow</div>
      </div>
      <div class="button-grid">
        <button id="pull" ${disabled}>Pull</button>
        <button id="pullFreeCM" ${disabled}>Pull Submodule</button>
        <button id="init" class="primary" ${disabled}>Init</button>
        <button id="update" class="primary" ${disabled}>Update</button>
      </div>
    </section>

    ${dependencyComparisonHtml}

    <section class="section" aria-labelledby="active-lock-title">
      <div class="section-header">
        <div id="active-lock-title" class="section-title">Active Lock</div>
      </div>
      <div class="target-description">source_roots.lock.jsonc</div>
      <div class="button-grid">
        <button id="usePinned" ${disabled}>Use pinned</button>
        <button id="pinLatest" ${disabled}>Pin latest</button>
        <button id="manualAll" ${disabled}>Manual all</button>
        <button id="updateUsed" ${disabled}>Update used</button>
      </div>
    </section>

    <section class="section" aria-labelledby="maintenance-title">
      <div class="section-header">
        <div id="maintenance-title" class="section-title">Maintenance</div>
      </div>
      <button id="cleanBuild" ${disabled}>Clean build</button>
    </section>

    <section class="section" aria-labelledby="repo-commands-title">
      <div class="section-header">
        <div id="repo-commands-title" class="section-title">Project Commands</div>
      </div>
      <div class="${repoCommandStatusClass}">${repoCommandMessage}</div>
      <div class="command-list">
        ${commandRows}
      </div>
    </section>

    ${codeCountHtml}
  </main>
  ${scriptUri === undefined ? "" : `<script nonce="${nonce}" src="${escapeHtml(scriptUri)}"></script>`}
</body>
</html>`;
}

export function emptyRepoCommandViewState(): RepoCommandViewState {
  return {
    status: "missing",
    message: undefined,
    actions: emptyRepoCommandActionViewStates(),
  };
}

export function emptyCodeCountViewState(): CodeCountViewState {
  return {
    enabled: false,
    targetPath: undefined,
    targetLabel: undefined,
    outputLabel: undefined,
  };
}

export function emptyDependencyComparison(): DependencyComparisonViewState {
  return {
    status: "empty",
    sampleMode: undefined,
    activeMode: undefined,
    rows: [],
  };
}

export function unavailableDependencyComparison(): DependencyComparisonViewState {
  return {
    status: "unavailable",
    sampleMode: undefined,
    activeMode: undefined,
    rows: [],
  };
}

export function dependencyComparisonViewState(
  comparison: DependencyComparison,
): DependencyComparisonViewState {
  return {
    status: comparison.rows.length === 0 ? "empty" : "ready",
    sampleMode: comparison.sampleMode,
    activeMode: comparison.activeMode,
    rows: comparison.rows.map((row) => ({
      name: row.name,
      samplePresent: row.samplePresent,
      sampleCommit: row.sampleCommit,
      activePresent: row.activePresent,
      activeCommit: row.activeCommit,
      activeMode: row.activeMode,
    })),
  };
}

export function emptyRepoCommandActionViewStates(): Record<
  RepoCommandAction,
  RepoCommandActionViewState
> {
  return Object.fromEntries(
    REPO_COMMAND_ACTIONS.map((action) => [
      action,
      {
        action,
        enabled: false,
        selectedLabel: undefined,
        variantCount: 0,
      },
    ]),
  ) as Record<RepoCommandAction, RepoCommandActionViewState>;
}

export function repoCommandActionViewStateFromSelection(
  action: RepoCommandAction,
  variants: readonly RepoCommandVariant[],
  selectedId: string | undefined,
  defaultVariant?: RepoCommandVariant,
): RepoCommandActionViewState {
  const explicitSelected =
    selectedId === undefined
      ? undefined
      : variants.find((variant) => variant.id === selectedId);
  const selected = explicitSelected ?? defaultVariant;
  return {
    action,
    enabled: selected !== undefined,
    selectedLabel: selected?.label,
    variantCount: variants.length,
  };
}

function webviewNonce(): string {
  return randomBytes(16).toString("base64");
}

function repoCommandRowHtml(
  actionState: RepoCommandActionViewState,
  globalDisabled: string,
): string {
  const disabled = globalDisabled !== "" || actionState.variantCount === 0 ? "disabled" : "";
  const selectDisabled =
    globalDisabled !== "" || actionState.variantCount === 0 ? "disabled" : "";
  const label = `${titleCase(actionState.action)}: ${
    actionState.selectedLabel === undefined
      ? "Select..."
      : escapeHtml(actionState.selectedLabel)
  }`;
  return `<div class="command-row">
    <button class="run" title="${label}" data-command="${actionState.action}" ${disabled}><span class="label">${label}</span></button>
    <button class="select" title="Select ${titleCase(
      actionState.action,
    )}" aria-label="Select ${titleCase(
      actionState.action,
    )} variant" data-command="${selectCommandForRepoAction(actionState.action)}" ${selectDisabled}>▾</button>
  </div>`;
}

function codeCountSectionHtml(
  codeCount: CodeCountViewState,
  globalDisabled: string,
): string {
  const disabled = globalDisabled !== "" || !codeCount.enabled ? "disabled" : "";
  const targetLabel = escapeHtml(codeCount.targetLabel ?? ".");
  const targetTitle = escapeHtml(codeCount.targetPath ?? "");
  return `<section class="section" aria-labelledby="code-count-title">
    <div class="section-header">
      <div id="code-count-title" class="section-title">Code Count</div>
    </div>
    <div class="path-row">
      <div class="path-value" title="${targetTitle}">${targetLabel}</div>
      <button id="resetCountPath" class="icon-button" title="Reset path" aria-label="Reset code count path" ${disabled}>↺</button>
      <button id="changeCountPath" class="icon-button" title="Change path" aria-label="Change code count path" ${disabled}>⋯</button>
      <button id="countCode" class="icon-button count-icon" title="Count code" aria-label="Count code" ${disabled}>▶</button>
    </div>
    <div class="count-target-label" title="${targetTitle}">${targetTitle}</div>
  </section>`;
}

function dependencyComparisonSectionHtml(
  comparison: DependencyComparisonViewState,
): string {
  if (comparison.status === "unavailable") {
    return `<section class="section" aria-labelledby="dependencies-title">
      <div class="section-header">
        <div id="dependencies-title" class="section-title">Dependencies</div>
      </div>
      <div class="dependency-empty">Dependency status unavailable</div>
    </section>`;
  }
  if (comparison.status === "empty") {
    return `<section class="section" aria-labelledby="dependencies-title">
      <div class="section-header">
        <div id="dependencies-title" class="section-title">Dependencies</div>
      </div>
      <div class="dependency-empty">No direct dependencies</div>
    </section>`;
  }

  const rows = comparison.rows.map((row) => {
    const name = escapeHtml(row.name);
    const mismatch = pinnedCommitsMismatch(comparison, row);
    const title = mismatch
      ? ` title="Pinned commit mismatch: sample ${escapeHtml(
          row.sampleCommit ?? "?",
        )}, active ${escapeHtml(row.activeCommit ?? "?")}"`
      : "";
    return `<div class="dependency-row${mismatch ? " mismatch" : ""}"${title}>
      <span class="dependency-name" title="${name}">${name}</span>
      ${dependencyStateHtml(comparison.sampleMode, row.samplePresent, row.sampleCommit)}
      ${dependencyStateHtml(row.activeMode, row.activePresent, row.activeCommit)}
    </div>`;
  }).join("");

  return `<section class="section" aria-labelledby="dependencies-title">
    <div class="section-header">
      <div id="dependencies-title" class="section-title">Dependencies</div>
    </div>
    <div class="dependency-table">
      <div class="dependency-row dependency-head">
        <span class="dependency-name">Dep</span>
        <span>Sample</span>
        <span>Active</span>
      </div>
      ${rows}
    </div>
  </section>`;
}

function dependencyStateHtml(
  mode: string | undefined,
  present: boolean,
  commit: string | undefined,
): string {
  if (!present) {
    return `<span class="dependency-state missing" title="Dependency not present">-</span>`;
  }
  const cssClass = dependencyModeClass(mode);
  const label = dependencyStateLabel(mode, commit);
  const title = mode === undefined
    ? "Unavailable"
    : commit === undefined
      ? escapeHtml(mode)
      : `${escapeHtml(mode)} ${escapeHtml(commit)}`;
  return `<span class="dependency-state ${cssClass}" title="${title}">${label}</span>`;
}

function pinnedCommitsMismatch(
  comparison: DependencyComparisonViewState,
  row: DependencyComparisonRowViewState,
): boolean {
  return (
    comparison.sampleMode === "pinned" &&
    row.activeMode === "pinned" &&
    row.samplePresent &&
    row.activePresent &&
    row.sampleCommit !== undefined &&
    row.activeCommit !== undefined &&
    row.sampleCommit !== row.activeCommit
  );
}

function dependencyModeSymbol(mode: string | undefined): string {
  if (mode === "pinned") {
    return "P";
  }
  if (mode === "latest") {
    return "L";
  }
  if (mode === "manual") {
    return "M";
  }
  return "?";
}

function dependencyStateLabel(mode: string | undefined, commit: string | undefined): string {
  if (mode === "pinned") {
    return commit === undefined ? "?" : escapeHtml(shortCommit(commit));
  }
  if (mode === "manual") {
    return "manual";
  }
  return dependencyModeSymbol(mode);
}

function shortCommit(commit: string): string {
  return commit.length <= 7 ? commit : commit.slice(0, 7);
}

function dependencyModeClass(mode: string | undefined): string {
  if (mode === "pinned" || mode === "latest" || mode === "manual") {
    return mode;
  }
  return "unknown";
}

function selectCommandForRepoAction(action: RepoCommandAction): RepoCommandSelectCommand {
  if (action === "config") {
    return "selectConfig";
  }
  if (action === "build") {
    return "selectBuild";
  }
  if (action === "test") {
    return "selectTest";
  }
  if (action === "run") {
    return "selectRun";
  }
  return "selectPackage";
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
