import * as vscode from "vscode";
import {
  RepoCommandAction,
  commandLinesForTerminal,
} from "../repoCommands";
import {
  activeRepoCommandConfiguration,
  repoCommandVariantsForSelection,
  withRepoCommandReadinessReceipt,
  withSelectedRepoCommandVariant,
  withoutRepoCommandReadinessReceipt,
} from "../repoCommandState";
import {
  repoCommandConfigurationSignature,
  repoCommandReadinessStatus,
} from "../repoCommandReadiness";
import { titleCase } from "../commands/repoCommandActions";
import { errorMessage } from "../terminal/terminalSessionManager";
import { RepoWorkspaceFolder } from "../workspaceDiscovery";
import { CommandControllerHost, warnIfLaunching } from "./commandHost";

export class RepoCommandController {
  constructor(private readonly host: CommandControllerHost) {}

  async runRepoCommand(action: RepoCommandAction): Promise<void> {
    if (warnIfLaunching(this.host)) {
      return;
    }

    let launched = false;
    let delegatedSelection = false;
    try {
      const folder = await this.host.resolveTargetFolderWithCapability(
        (capability) => capability.hasRepoCommandManifest,
        "No workspace with configs/freecm.commands.jsonc was found.",
        "Select FreeCM project command workspace",
        "Choose the workspace folder for this project command",
      );
      if (folder === undefined) {
        return;
      }
      const manifest = await this.host.loadRepoCommandsForFolder(folder);
      if (manifest === undefined) {
        this.host.logToTerminal(
          "warning",
          "No configs/freecm.commands.jsonc manifest was found.",
          folder,
        );
        return;
      }
      const selectionState = this.host.repoCommandSelectionState(folder);
      const configuration = activeRepoCommandConfiguration(
        manifest,
        selectionState,
      );
      const variant = this.host.selectedRepoCommandVariant(
        folder,
        manifest,
        action,
      );
      if (variant === undefined) {
        delegatedSelection = true;
        await this.selectRepoCommand(action, { folder });
        return;
      }
      if (action !== "config") {
        if (configuration === undefined) {
          this.host.logToTerminal(
            "warning",
            `Select Config before running ${titleCase(action)}.`,
            folder,
          );
          return;
        }
        const readiness = await repoCommandReadinessStatus(
          folder.fsPath,
          configuration,
          selectionState.readinessByConfig[configuration.id],
        );
        if (!readiness.ready) {
          this.host.logToTerminal(
            "warning",
            `Needs Config — ${readiness.reason}`,
            folder,
          );
          this.host.workspaceState.invalidateCache(folder.fsPath);
          await this.host.refresh();
          return;
        }
      }

      if (action === "config") {
        await this.host.updateRepoCommandSelectionState(
          folder,
          withoutRepoCommandReadinessReceipt(selectionState, variant.id),
        );
        this.host.workspaceState.invalidateCache(folder.fsPath);
      }

      launched = true;
      this.host.setLaunching(true);
      this.host.setStatusBarLaunchCommand(action);
      await this.host.refresh();
      const label = `${titleCase(action)}: ${variant.label}`;
      this.host.logToTerminal("info", `Running ${label}`, folder);
      const lines = commandLinesForTerminal(variant);
      const outcome = await this.host.executeInFreeCMTerminal(
        folder,
        label,
        () => this.host.terminalForRepoCommand(folder, action),
        lines,
      );
      if (action === "config") {
        if (outcome.status === "success") {
          const signature = await repoCommandConfigurationSignature(
            folder.fsPath,
            variant,
          );
          await this.host.updateRepoCommandSelectionState(
            folder,
            withRepoCommandReadinessReceipt(
              this.host.repoCommandSelectionState(folder),
              variant.id,
              {
                signature,
                completedAt: new Date().toISOString(),
              },
            ),
          );
          this.host.workspaceState.invalidateCache(folder.fsPath);
        } else if (outcome.status === "unknown") {
          this.host.logToTerminal(
            "warning",
            "Config completion could not be verified because terminal shell integration is unavailable; dependent commands remain blocked.",
            folder,
          );
        }
      }
    } catch (error) {
      this.host.logToTerminal("error", errorMessage(error));
    } finally {
      if (launched) {
        this.host.setLaunching(false);
        this.host.setStatusBarLaunchCommand(undefined);
        await this.host.refresh();
        this.host.finishTerminalLogGroup();
      } else if (!delegatedSelection) {
        this.host.finishTerminalLogGroup();
      }
    }
  }

  async selectRepoCommand(
    action: RepoCommandAction,
    options: {
      readonly folder?: RepoWorkspaceFolder;
      readonly skipRefresh?: boolean;
    } = {},
  ): Promise<void> {
    const folder =
      options.folder ??
      (await this.host.resolveTargetFolderWithCapability(
        (capability) => capability.hasRepoCommandManifest,
        "No workspace with configs/freecm.commands.jsonc was found.",
        "Select FreeCM project command workspace",
        "Choose the workspace folder for this project command",
      ));
    if (folder === undefined) {
      this.host.finishTerminalLogGroup();
      return;
    }
    try {
      const manifest = await this.host.loadRepoCommandsForFolder(folder);
      if (manifest === undefined) {
        this.host.logToTerminal(
          "warning",
          "No configs/freecm.commands.jsonc manifest was found.",
          folder,
        );
        return;
      }
      const selectionState = this.host.repoCommandSelectionState(folder);
      const variants = repoCommandVariantsForSelection(
        manifest,
        selectionState,
        action,
      );
      if (variants.length === 0) {
        this.host.logToTerminal(
          "warning",
          `No FreeCM ${action} command is available on this platform.`,
          folder,
        );
        return;
      }

      const current = this.host.selectedRepoCommandVariant(
        folder,
        manifest,
        action,
      );
      this.host.pausePanelSelectionRendering();
      try {
        const selected = await vscode.window.showQuickPick(
          variants.map((variant) => ({
            label: variant.label,
            description: variant.description,
            detail: commandLinesForTerminal(variant).join(" && "),
            picked: variant.id === current?.id,
            variant,
          })),
          {
            title: `Select FreeCM ${titleCase(action)} command`,
            placeHolder: `Choose the ${action} command variant for this workspace`,
          },
        );
        if (selected === undefined) {
          return;
        }

        await this.host.updateRepoCommandSelectionState(
          folder,
          withSelectedRepoCommandVariant(
            manifest,
            selectionState,
            action,
            selected.variant.id,
          ),
        );
        this.host.workspaceState.invalidateCache(folder.fsPath);
        this.host.logToTerminal(
          "success",
          `Selected ${titleCase(action)}: ${selected.variant.label}`,
          folder,
        );
        if (options.skipRefresh !== true) {
          await this.host.refresh();
        }
      } finally {
        this.host.resumePanelSelectionRendering();
      }
    } catch (error) {
      this.host.logToTerminal("error", errorMessage(error), folder);
    } finally {
      this.host.finishTerminalLogGroup();
    }
  }
}
