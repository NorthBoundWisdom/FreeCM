#!/usr/bin/env node
import {
  REPO_COMMAND_DEPENDENT_ACTIONS,
  RepoCommandManifestState,
  commandLinesForTerminal,
  compatibleRepoCommandVariants,
  defaultRepoCommandVariant,
  loadRepoCommandManifest,
  repoCommandWarnings,
} from "./repoCommands";

interface CliOptions {
  readonly repoRoot: string;
  readonly platform: string;
  readonly preview: boolean;
}

async function main(argv: readonly string[]): Promise<number> {
  try {
    const options = parseArgs(argv);
    if (options === "help") {
      printHelp();
      return 0;
    }

    const manifest = await loadRepoCommandManifest(
      options.repoRoot,
      options.platform,
    );
    if (manifest === undefined) {
      console.error(
        `FreeCM command manifest not found: ${options.repoRoot}/configs/freecm.commands.jsonc`,
      );
      return 1;
    }

    const warnings = repoCommandWarnings(manifest, options.platform);
    if (options.preview) {
      printPreview(manifest, options.platform);
    } else {
      console.log(`Validated ${manifest.manifestPath}`);
    }
    for (const warning of warnings) {
      console.error(
        `warning: ${warning.action}:${warning.variantId} step ${warning.stepIndex + 1}: ${warning.message}`,
      );
    }
    return 0;
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    return 1;
  }
}

function parseArgs(argv: readonly string[]): CliOptions | "help" {
  let repoRoot: string | undefined;
  let platform: string = process.platform;
  let preview = false;

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--help" || arg === "-h") {
      return "help";
    }
    if (arg === "--preview") {
      preview = true;
      continue;
    }
    if (arg === "--platform") {
      const value = argv[index + 1];
      if (value === undefined || value.startsWith("-")) {
        throw new Error("--platform requires a value");
      }
      platform = value;
      index += 1;
      continue;
    }
    if (arg.startsWith("-")) {
      throw new Error(`Unknown option: ${arg}`);
    }
    if (repoRoot !== undefined) {
      throw new Error(`Unexpected extra argument: ${arg}`);
    }
    repoRoot = arg;
  }

  return {
    repoRoot: repoRoot ?? process.cwd(),
    platform,
    preview,
  };
}

function printPreview(
  manifest: RepoCommandManifestState,
  platform: string,
): void {
  for (const configuration of manifest.configurations) {
    const defaultSuffix =
      configuration.id === manifest.defaultConfiguration?.id
        ? " (default)"
        : "";
    console.log(`Configuration: ${configuration.label}${defaultSuffix}`);
    console.log(`  Config: ${configuration.label}`);
    for (const line of commandLinesForTerminal(configuration, platform)) {
      console.log(`    ${line}`);
    }
    for (const action of REPO_COMMAND_DEPENDENT_ACTIONS) {
      const defaultVariant = defaultRepoCommandVariant(
        manifest,
        configuration.id,
        action,
      );
      for (const variant of compatibleRepoCommandVariants(
        manifest,
        configuration.id,
        action,
      )) {
        const variantDefaultSuffix =
          variant.id === defaultVariant?.id ? " (default)" : "";
        console.log(
          `  ${titleCase(action)}: ${variant.label}${variantDefaultSuffix}`,
        );
        for (const line of commandLinesForTerminal(variant, platform)) {
          console.log(`    ${line}`);
        }
      }
    }
    if (configuration !== manifest.configurations.at(-1)) {
      console.log("");
    }
  }
}

function printHelp(): void {
  console.log(`Usage: node out/validateRepoCommands.js [--preview] [--platform darwin|linux|win32] [repoRoot]

Validates configs/freecm.commands.jsonc with the same parser and terminal quoting used by the FreeCM VS Code extension.

Options:
  --preview       Group Config-compatible variants and print their exact terminal lines.
  --platform      Validate and preview variants for a specific Node process.platform value.
  -h, --help      Show this help.
`);
}

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

if (require.main === module) {
  main(process.argv.slice(2)).then((code) => {
    process.exitCode = code;
  });
}
