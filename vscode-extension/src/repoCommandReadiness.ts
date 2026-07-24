import { createHash } from "crypto";
import * as fs from "fs/promises";
import * as path from "path";
import { beginFilesystemRead } from "./performanceMetrics";
import { RepoCommandReadinessReceipt } from "./repoCommandState";
import { RepoCommandVariant } from "./repoCommands";

export interface RepoCommandReadinessStatus {
  readonly ready: boolean;
  readonly signature: string;
  readonly reason?: string;
}

export async function repoCommandConfigurationSignature(
  repoRoot: string,
  configuration: RepoCommandVariant,
): Promise<string> {
  const readiness = configuration.readiness ?? { inputs: [], outputs: [] };
  const inputs = await Promise.all(
    readiness.inputs.map(async (relativePath) => ({
      path: relativePath,
      contents: await readInput(repoRoot, relativePath),
    })),
  );
  return createHash("sha256")
    .update(
      JSON.stringify({
        version: 1,
        configurationId: configuration.id,
        steps: configuration.steps,
        inputs,
      }),
    )
    .digest("hex");
}

export async function repoCommandReadinessStatus(
  repoRoot: string,
  configuration: RepoCommandVariant,
  receipt: RepoCommandReadinessReceipt | undefined,
): Promise<RepoCommandReadinessStatus> {
  const signature = await repoCommandConfigurationSignature(
    repoRoot,
    configuration,
  );
  if (receipt === undefined) {
    return {
      ready: false,
      signature,
      reason: `Run Config: ${configuration.label}`,
    };
  }
  if (receipt.signature !== signature) {
    return {
      ready: false,
      signature,
      reason: `Config inputs changed; rerun Config: ${configuration.label}`,
    };
  }

  for (const output of configuration.readiness?.outputs ?? []) {
    if (!(await repoPathExists(repoRoot, output))) {
      return {
        ready: false,
        signature,
        reason: `Config output is missing: ${output}`,
      };
    }
  }
  return { ready: true, signature };
}

async function readInput(
  repoRoot: string,
  relativePath: string,
): Promise<string> {
  const finishRead = beginFilesystemRead();
  try {
    return await fs.readFile(path.join(repoRoot, relativePath), "utf8");
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) {
      return "<missing>";
    }
    throw error;
  } finally {
    finishRead();
  }
}

async function repoPathExists(
  repoRoot: string,
  relativePath: string,
): Promise<boolean> {
  const finishRead = beginFilesystemRead();
  try {
    await fs.access(path.join(repoRoot, relativePath));
    return true;
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) {
      return false;
    }
    throw error;
  } finally {
    finishRead();
  }
}

function isNodeErrorCode(error: unknown, code: string): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === code
  );
}
