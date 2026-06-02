import { randomBytes } from "crypto";
import { DependencyComparison } from "../lockWorkflow";
import {
  REPO_COMMAND_ACTIONS,
  RepoCommandAction,
  RepoCommandVariant,
} from "../repoCommands";
import { EXTENSION_BUILD_INFO } from "../buildInfo";
import { RepoCommandSelectCommand } from "./messageProtocol";

export interface WorkflowViewState {
  readonly workspaceCount: number;
  readonly targetName: string | undefined;
  readonly launching: boolean;
  readonly commands: WorkflowCommandAvailability;
  readonly lockMode: string | undefined;
  readonly lockStatusUnavailable: boolean;
  readonly dependencyComparison: DependencyComparisonViewState;
  readonly repoCommands: RepoCommandViewState;
  readonly codeCount: CodeCountViewState;
}

export interface WorkflowCommandAvailability {
  readonly pull: boolean;
  readonly pullFreeCM: boolean;
  readonly init: boolean;
  readonly update: boolean;
  readonly cleanBuild: boolean;
  readonly usePinned: boolean;
  readonly pinLatest: boolean;
  readonly manualAll: boolean;
  readonly updateUsed: boolean;
}

export interface CodeCountViewState {
  readonly enabled: boolean;
  readonly targetPath: string | undefined;
  readonly targetLabel: string | undefined;
  readonly outputLabel: string | undefined;
  readonly excludePaths: readonly string[];
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
  const hasWorkspace = state.workspaceCount > 0;
  const targetLabel =
    state.targetName === undefined
      ? hasWorkspace
        ? "Multiple workspaces"
        : "No workspace"
      : escapeHtml(state.targetName);
  const statusClass = hasWorkspace ? "ready" : "empty";
  const disabled = (enabled: boolean): string =>
    !enabled || state.launching ? "disabled" : "";
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
  const codeCountDisabled = state.launching ? "disabled" : "";
  const codeCountHtml = codeCountSectionHtml(state.codeCount, codeCountDisabled);
  const commandRows = REPO_COMMAND_ACTIONS.map((action) =>
    repoCommandRowHtml(state.repoCommands.actions[action], state.launching),
  ).join("");
  const workflowMessage =
    state.commands.init || state.commands.update
      ? ""
      : `<div class="command-status">No configs/source_root_workflow.py found</div>`;
  const activeLockMessage =
    state.commands.usePinned || state.commands.manualAll || state.commands.updateUsed
      ? ""
      : `<div class="command-status">No source_roots lock file found</div>`;

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
        <button id="pull" ${disabled(state.commands.pull)}>Pull</button>
        <button id="pullFreeCM" ${disabled(state.commands.pullFreeCM)}>Pull Submodule</button>
        <button id="init" class="primary" ${disabled(state.commands.init)}>Init</button>
        <button id="update" class="primary" ${disabled(state.commands.update)}>Update</button>
      </div>
      ${workflowMessage}
    </section>

    ${dependencyComparisonHtml}

    <section class="section" aria-labelledby="active-lock-title">
      <div class="section-header">
        <div id="active-lock-title" class="section-title">Active Lock</div>
      </div>
      <div class="target-description">source_roots.lock.jsonc</div>
      <div class="button-grid">
        <button id="usePinned" ${disabled(state.commands.usePinned)}>Use pinned</button>
        <button id="pinLatest" ${disabled(state.commands.pinLatest)}>Pin latest</button>
        <button id="manualAll" ${disabled(state.commands.manualAll)}>Manual all</button>
        <button id="updateUsed" ${disabled(state.commands.updateUsed)}>Update used</button>
      </div>
      ${activeLockMessage}
    </section>

    <section class="section" aria-labelledby="maintenance-title">
      <div class="section-header">
        <div id="maintenance-title" class="section-title">Maintenance</div>
      </div>
      <button id="cleanBuild" ${disabled(state.commands.cleanBuild)}>Clean build</button>
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
    excludePaths: [],
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
  launching: boolean,
): string {
  const disabled = launching || !actionState.enabled ? "disabled" : "";
  const selectDisabled =
    launching || actionState.variantCount === 0 ? "disabled" : "";
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
  const excludePaths = codeCount.excludePaths.map((name) => escapeHtml(name));
  const excludeTitle = excludePaths.length === 0
    ? "Custom excludes"
    : excludePaths.join("&#10;");
  const excludePreviewHtml = excludePaths.length === 0
    ? `<div class="filter-placeholder">Custom excludes</div>`
    : excludePaths.map((name) =>
      `<div class="filter-line" title="${name}">${name}</div>`,
    ).join("");
  const excludeEditorValue = escapeHtml(codeCount.excludePaths.join("\n"));
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
    <div class="filter-panel">
      <div id="countExcludePreview" class="filter-preview">
        <div class="filter-lines" title="${excludeTitle}">${excludePreviewHtml}</div>
        <button id="editCountExcludePaths" class="icon-button" title="Edit excluded paths" aria-label="Edit code count excluded paths" ${disabled}>✎</button>
      </div>
      <div id="countExcludeEditor" class="filter-edit" hidden>
        <textarea id="countExcludePathsText" class="filter-textarea" aria-label="Code count excluded paths" spellcheck="false" ${disabled}>${excludeEditorValue}</textarea>
        <button id="saveCountExcludePaths" class="icon-button count-icon" title="Save excluded paths" aria-label="Save code count excluded paths" ${disabled}>✓</button>
        <button id="cancelCountExcludePaths" class="icon-button" title="Cancel excluded path edits" aria-label="Cancel code count excluded path edits" ${disabled}>×</button>
      </div>
    </div>
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
