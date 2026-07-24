import { randomBytes } from "crypto";
import { DependencyComparison, ManualPathStatus } from "../lockWorkflow";
import {
  REPO_COMMAND_ACTIONS,
  RepoCommandAction,
  RepoCommandVariant,
} from "../repoCommands";
import { titleCase } from "../commands/repoCommandActions";
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
  readonly pullSeeds: boolean;
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
  readonly activeManualPath?: string | undefined;
  readonly activeManualPathStatus?: ManualPathStatus | undefined;
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
  readonly blockedReason?: string;
}

interface WorkflowViewHtmlResources {
  readonly nonce?: string;
  readonly cspSource?: string;
  readonly scriptUri?: string;
  readonly styleUri?: string;
}

export const WORKFLOW_REGION_IDS = [
  "freecm-target",
  "freecm-workflow",
  "freecm-dependencies",
  "freecm-active-lock",
  "freecm-maintenance",
  "freecm-repo-commands",
  "freecm-code-count",
] as const;

export type WorkflowRegionId = (typeof WORKFLOW_REGION_IDS)[number];

export function workflowViewHtml(
  state: WorkflowViewState,
  resources: WorkflowViewHtmlResources | string = {},
): string {
  const nonce =
    typeof resources === "string"
      ? resources
      : (resources.nonce ?? webviewNonce());
  const cspSource =
    typeof resources === "string" ? "" : (resources.cspSource ?? "");
  const styleSource = cspSource === "" ? "'none'" : escapeHtml(cspSource);
  const scriptUri =
    typeof resources === "string" ? undefined : resources.scriptUri;
  const styleUri =
    typeof resources === "string" ? undefined : resources.styleUri;
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
    state.repoCommands.message === undefined
      ? ""
      : escapeHtml(state.repoCommands.message);
  const repoCommandsNeedConfig =
    state.repoCommands.status === "ready" &&
    REPO_COMMAND_ACTIONS.some(
      (action) =>
        action !== "config" &&
        state.repoCommands.actions[action].blockedReason !== undefined,
    );
  const repoCommandStatusClass =
    state.repoCommands.status === "error"
      ? "command-status error"
      : repoCommandsNeedConfig
        ? "command-status warning"
        : "command-status";
  const dependencyComparisonHtml = dependencyComparisonSectionHtml(
    state.dependencyComparison,
    state.launching,
  );
  const codeCountDisabled = state.launching ? "disabled" : "";
  const codeCountHtml = codeCountSectionHtml(
    state.codeCount,
    codeCountDisabled,
  );
  const commandRows = REPO_COMMAND_ACTIONS.map((action) =>
    repoCommandRowHtml(state.repoCommands.actions[action], state.launching),
  ).join("");
  const workflowMessage =
    state.commands.init || state.commands.update
      ? ""
      : `<div class="command-status">No configs/source_root_workflow.py found</div>`;
  const activeLockMessage =
    state.commands.usePinned ||
    state.commands.manualAll ||
    state.commands.updateUsed
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
    <!-- freecm-region-start:freecm-target -->
    <section id="freecm-target" class="target-card ${statusClass}">
      <div class="build-info">${buildInfoText}</div>
      <div class="target-name" title="${targetLabel}">${targetLabel}</div>
    </section>
    <!-- freecm-region-end:freecm-target -->

    <!-- freecm-region-start:freecm-workflow -->
    <section id="freecm-workflow" class="section" aria-labelledby="workflow-title">
      <div class="section-header">
        <div id="workflow-title" class="section-title">Workflow</div>
      </div>
      <div class="button-grid">
        <button id="pull" ${disabled(state.commands.pull)}>Pull</button>
        <button id="pullSeeds" ${disabled(state.commands.pullSeeds)}>Pull Seeds</button>
        <button id="init" class="primary" ${disabled(state.commands.init)}>Init</button>
        <button id="update" class="primary" ${disabled(state.commands.update)}>Update</button>
      </div>
      ${workflowMessage}
    </section>
    <!-- freecm-region-end:freecm-workflow -->

    <!-- freecm-region-start:freecm-dependencies -->
    ${dependencyComparisonHtml}
    <!-- freecm-region-end:freecm-dependencies -->

    <!-- freecm-region-start:freecm-active-lock -->
    <section id="freecm-active-lock" class="section" aria-labelledby="active-lock-title">
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
    <!-- freecm-region-end:freecm-active-lock -->

    <!-- freecm-region-start:freecm-maintenance -->
    <section id="freecm-maintenance" class="section" aria-labelledby="maintenance-title">
      <div class="section-header">
        <div id="maintenance-title" class="section-title">Maintenance</div>
      </div>
      <button id="cleanBuild" ${disabled(state.commands.cleanBuild)}>Clean build</button>
    </section>
    <!-- freecm-region-end:freecm-maintenance -->

    <!-- freecm-region-start:freecm-repo-commands -->
    <section id="freecm-repo-commands" class="section" aria-labelledby="repo-commands-title">
      <div class="section-header">
        <div id="repo-commands-title" class="section-title">Project Commands</div>
      </div>
      <div class="${repoCommandStatusClass}">${repoCommandMessage}</div>
      <div class="command-list">
        ${commandRows}
      </div>
    </section>
    <!-- freecm-region-end:freecm-repo-commands -->

    <!-- freecm-region-start:freecm-code-count -->
    ${codeCountHtml}
    <!-- freecm-region-end:freecm-code-count -->
  </main>
  ${scriptUri === undefined ? "" : `<script nonce="${nonce}" src="${escapeHtml(scriptUri)}"></script>`}
</body>
</html>`;
}

export function workflowViewRegions(
  state: WorkflowViewState,
): Record<WorkflowRegionId, string> {
  const html = workflowViewHtml(state, { nonce: "region-snapshot" });
  return Object.fromEntries(
    WORKFLOW_REGION_IDS.map((id) => {
      const startMarker = `<!-- freecm-region-start:${id} -->`;
      const endMarker = `<!-- freecm-region-end:${id} -->`;
      const start = html.indexOf(startMarker);
      const end = html.indexOf(endMarker);
      if (start < 0 || end < start) {
        throw new Error(`Workflow region ${id} is missing`);
      }
      return [id, html.slice(start + startMarker.length, end).trim()];
    }),
  ) as Record<WorkflowRegionId, string>;
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
      activeManualPath: row.activeManualPath,
      activeManualPathStatus: row.activeManualPathStatus,
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
        blockedReason: undefined,
      },
    ]),
  ) as Record<RepoCommandAction, RepoCommandActionViewState>;
}

export function repoCommandActionViewStateFromSelection(
  action: RepoCommandAction,
  variants: readonly RepoCommandVariant[],
  selected: RepoCommandVariant | undefined,
  blockedReason?: string,
): RepoCommandActionViewState {
  return {
    action,
    enabled: variants.length > 0 && blockedReason === undefined,
    selectedLabel: selected?.label,
    variantCount: variants.length,
    blockedReason,
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
  const actionLabel = titleCase(actionState.action);
  const label = `${actionLabel}: ${
    actionState.selectedLabel === undefined
      ? "Select..."
      : escapeHtml(actionState.selectedLabel)
  }`;
  const title =
    actionState.blockedReason === undefined
      ? label
      : `${label} — ${escapeHtml(actionState.blockedReason)}`;
  return `<div class="command-row">
    <button class="run" title="${title}" data-command="${actionState.action}" ${disabled}><span class="label">${label}</span></button>
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
  const disabled =
    globalDisabled !== "" || !codeCount.enabled ? "disabled" : "";
  const targetLabel = escapeHtml(codeCount.targetLabel ?? ".");
  const targetTitle = escapeHtml(codeCount.targetPath ?? "");
  const excludePaths = codeCount.excludePaths.map((name) => escapeHtml(name));
  const excludeTitle =
    excludePaths.length === 0 ? "Custom excludes" : excludePaths.join("&#10;");
  const excludePreviewHtml =
    excludePaths.length === 0
      ? `<div class="filter-placeholder">Custom excludes</div>`
      : excludePaths
          .map(
            (name) => `<div class="filter-line" title="${name}">${name}</div>`,
          )
          .join("");
  const excludeEditorValue = escapeHtml(codeCount.excludePaths.join("\n"));
  return `<section id="freecm-code-count" class="section" aria-labelledby="code-count-title">
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
  launching: boolean,
): string {
  if (comparison.status === "unavailable") {
    return `<section id="freecm-dependencies" class="section" aria-labelledby="dependencies-title">
      <div class="section-header">
        <div id="dependencies-title" class="section-title">Dependencies</div>
      </div>
      <div class="dependency-empty">Dependency status unavailable</div>
    </section>`;
  }
  if (comparison.status === "empty") {
    return `<section id="freecm-dependencies" class="section" aria-labelledby="dependencies-title">
      <div class="section-header">
        <div id="dependencies-title" class="section-title">Dependencies</div>
      </div>
      <div class="dependency-empty">No direct dependencies</div>
    </section>`;
  }

  const rows = comparison.rows
    .map((row) => {
      const name = escapeHtml(row.name);
      const mismatch = pinnedCommitsMismatch(comparison, row);
      const title = mismatch
        ? ` title="Pinned commit mismatch: sample ${escapeHtml(
            row.sampleCommit ?? "?",
          )}, active ${escapeHtml(row.activeCommit ?? "?")}"`
        : "";
      return `<div class="dependency-row${mismatch ? " mismatch" : ""}"${title}>
      <span class="dependency-name" title="${name}">${name}</span>
      ${dependencyStateHtml(
        comparison.sampleMode,
        row.samplePresent,
        row.sampleCommit,
        undefined,
        undefined,
        sampleDependencyAction(row, launching),
      )}
      ${dependencyStateHtml(
        row.activeMode,
        row.activePresent,
        row.activeCommit,
        row.activeManualPathStatus,
        row.activeManualPath,
        activeDependencyAction(row, launching),
      )}
    </div>`;
    })
    .join("");

  return `<section id="freecm-dependencies" class="section" aria-labelledby="dependencies-title">
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
  manualPathStatus?: ManualPathStatus | undefined,
  manualPath?: string | undefined,
  action?: DependencyStateAction | undefined,
): string {
  if (!present) {
    return `<span class="dependency-cell"><span class="dependency-state missing" title="Dependency not present">-</span></span>`;
  }
  const cssClass = dependencyModeClass(mode, manualPathStatus);
  const label = dependencyStateLabel(mode, commit, manualPathStatus);
  const title = dependencyStateTitle(
    mode,
    commit,
    manualPathStatus,
    manualPath,
  );
  const actionHtml =
    action === undefined ? "" : dependencyStateActionButton(action);
  return `<span class="dependency-cell"><span class="dependency-state ${cssClass}" title="${title}">${label}</span>${actionHtml}</span>`;
}

interface DependencyStateAction {
  readonly command:
    | "applyActiveDependencyToSample"
    | "manualDependency"
    | "restoreDependencyPin";
  readonly dependency: string;
  readonly label: string;
  readonly title: string;
  readonly disabled: boolean;
}

function sampleDependencyAction(
  row: DependencyComparisonRowViewState,
  launching: boolean,
): DependencyStateAction | undefined {
  if (!row.samplePresent || !row.activePresent) {
    return undefined;
  }
  return {
    command: "applyActiveDependencyToSample",
    dependency: row.name,
    label: "<-",
    title: `Apply active ${row.name} to sample`,
    disabled: launching,
  };
}

function activeDependencyAction(
  row: DependencyComparisonRowViewState,
  launching: boolean,
): DependencyStateAction | undefined {
  if (!row.activePresent || row.activeCommit === undefined) {
    return undefined;
  }
  if (row.activeMode === "manual") {
    return {
      command: "restoreDependencyPin",
      dependency: row.name,
      label: "R",
      title: `Restore pinned dependency for ${row.name}`,
      disabled: launching,
    };
  }
  if (row.activeMode !== "pinned") {
    return undefined;
  }
  return {
    command: "manualDependency",
    dependency: row.name,
    label: "M",
    title: `Use manual seed path for ${row.name}`,
    disabled: launching,
  };
}

function dependencyStateActionButton(action: DependencyStateAction): string {
  const disabled = action.disabled ? "disabled" : "";
  const title = escapeHtml(action.title);
  const label = escapeHtml(action.label);
  const dependency = escapeHtml(action.dependency);
  return (
    `<button class="dependency-state-action" title="${title}" aria-label="${title}" ` +
    `data-command="${action.command}" data-dependency="${dependency}" ${disabled}>${label}</button>`
  );
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

function dependencyStateLabel(
  mode: string | undefined,
  commit: string | undefined,
  manualPathStatus?: ManualPathStatus | undefined,
): string {
  if (mode === "pinned") {
    return commit === undefined ? "?" : escapeHtml(shortCommit(commit));
  }
  if (mode === "manual") {
    return manualPathStatus === undefined
      ? "manual"
      : manualPathStatusLabel(manualPathStatus);
  }
  return dependencyModeSymbol(mode);
}

function dependencyStateTitle(
  mode: string | undefined,
  commit: string | undefined,
  manualPathStatus?: ManualPathStatus | undefined,
  manualPath?: string | undefined,
): string {
  if (mode === undefined) {
    return "Unavailable";
  }
  if (mode === "manual" && manualPathStatus !== undefined) {
    const pathTitle =
      manualPath === undefined ? "" : `: ${escapeHtml(manualPath)}`;
    return `${manualPathStatusTitle(manualPathStatus)}${pathTitle}`;
  }
  return commit === undefined
    ? escapeHtml(mode)
    : `${escapeHtml(mode)} ${escapeHtml(commit)}`;
}

function manualPathStatusLabel(status: ManualPathStatus): string {
  if (status === "clean") {
    return "M(Clean)";
  }
  if (status === "dirty") {
    return "M(dirty)";
  }
  return "M(U)";
}

function manualPathStatusTitle(status: ManualPathStatus): string {
  if (status === "clean") {
    return "manual clean";
  }
  if (status === "dirty") {
    return "manual dirty";
  }
  return "manual untracked or unavailable";
}

function shortCommit(commit: string): string {
  return commit.length <= 7 ? commit : commit.slice(0, 7);
}

function dependencyModeClass(
  mode: string | undefined,
  manualPathStatus?: ManualPathStatus | undefined,
): string {
  if (mode === "manual" && manualPathStatus !== undefined) {
    return `manual manual-${manualPathStatus}`;
  }
  if (mode === "pinned" || mode === "latest" || mode === "manual") {
    return mode;
  }
  return "unknown";
}

function selectCommandForRepoAction(
  action: RepoCommandAction,
): RepoCommandSelectCommand {
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
