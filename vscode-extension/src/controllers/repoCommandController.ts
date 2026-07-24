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
} from "../repoCommandState";
import {
  repoCommandConfigurationSignature,
  repoCommandReadinessStatus,
} from "../repoCommandReadiness";
import { titleCase } from "../commands/repoCommandActions";
import { errorMessage } from "../terminal/terminalSessionManager";
import { RepoWorkspaceFolder } from "../workspaceDiscovery";
import { CommandControllerHost } from "./commandHost";

export class RepoCommandController {
  private dispatchQueue: Promise<void> = Promise.resolve();

  constructor(private readonly host: CommandControllerHost) {}

  runRepoCommand(action: RepoCommandAction): Promise<void> {
    const queued = this.dispatchQueue.then(() =>
      this.dispatchRepoCommand(action),
    );
    this.dispatchQueue = queued.catch(() => undefined);
    return queued;
  }

  private async dispatchRepoCommand(
    action: RepoCommandAction,
  ): Promise<void> {
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

      const label = `${titleCase(action)}: ${variant.label}`;
      const lines = commandLinesForTerminal(variant);
      const signature =
        action === "config"
          ? await repoCommandConfigurationSignature(folder.fsPath, variant)
          : undefined;
      await this.host.queueInFreeCMTerminal(
        folder,
        () => this.host.terminalForRepoCommand(folder),
        lines,
      );
      this.host.logToTerminal("success", `Queued ${label}`, folder);
      if (action === "config") {
        if (signature === undefined) {
          throw new Error("Config signature was not prepared");
        }
        await this.host.updateRepoCommandSelectionState(
          folder,
          withRepoCommandReadinessReceipt(
            this.host.repoCommandSelectionState(folder),
            variant.id,
            {
              signature,
              submittedAt: new Date().toISOString(),
            },
          ),
        );
        this.host.workspaceState.invalidateCache(folder.fsPath);
        await this.host.refresh();
      }
    } catch (error) {
      this.host.logToTerminal("error", errorMessage(error));
    } finally {
      if (!delegatedSelection) {
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
