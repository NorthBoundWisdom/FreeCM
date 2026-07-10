import * as fs from "fs/promises";
import * as path from "path";

const ATOMIC_SIDECAR_DIRECTORY = path.join(".freecm", "atomic");
const WINDOWS_RENAME_RETRY_DELAYS_MS = [10, 20, 40, 80, 160] as const;

type RenameFunction = (source: string, target: string) => Promise<void>;

interface RenameRetryDependencies {
  readonly platform: NodeJS.Platform;
  readonly rename: RenameFunction;
  readonly delay: (milliseconds: number) => Promise<void>;
}

export async function atomicWriteText(
  filePath: string,
  text: string,
): Promise<void> {
  const directory = path.dirname(filePath);
  const baseName = path.basename(filePath);
  const sidecarDirectory = atomicSidecarDirectory(filePath);
  const tempPath = path.join(
    sidecarDirectory,
    `.${baseName}.${process.pid}.${Date.now()}.${Math.random()
      .toString(16)
      .slice(2)}.tmp`,
  );

  try {
    await fs.mkdir(sidecarDirectory, { recursive: true });
    const handle = await fs.open(tempPath, "wx");
    try {
      await handle.writeFile(text, "utf8");
      await handle.sync();
    } finally {
      await handle.close();
    }
    await renameReplacingWithRetry(tempPath, filePath);
    await fsyncDirectory(directory);
  } catch (error) {
    await removeIfExists(tempPath);
    throw error;
  }
}

async function renameReplacingWithRetry(
  source: string,
  target: string,
  dependencies: RenameRetryDependencies = {
    platform: process.platform,
    rename: fs.rename,
    delay,
  },
): Promise<void> {
  let retryIndex = 0;
  while (true) {
    try {
      await dependencies.rename(source, target);
      return;
    } catch (error) {
      if (
        dependencies.platform !== "win32" ||
        !isTransientWindowsRenameError(error) ||
        retryIndex >= WINDOWS_RENAME_RETRY_DELAYS_MS.length
      ) {
        throw error;
      }
      await dependencies.delay(WINDOWS_RENAME_RETRY_DELAYS_MS[retryIndex]);
      retryIndex += 1;
    }
  }
}

function isTransientWindowsRenameError(error: unknown): boolean {
  const code = (error as NodeJS.ErrnoException | undefined)?.code;
  return code === "EPERM" || code === "EBUSY" || code === "EACCES";
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function fsyncDirectory(directory: string): Promise<void> {
  try {
    const handle = await fs.open(directory, "r");
    try {
      await handle.sync();
    } finally {
      await handle.close();
    }
  } catch {
    // Directory fsync is not supported consistently across platforms.
  }
}

async function removeIfExists(filePath: string): Promise<void> {
  try {
    await fs.rm(filePath, { force: true });
  } catch {
    // Best-effort cleanup only; the original write error is more useful.
  }
}

function atomicSidecarDirectory(filePath: string): string {
  return path.join(path.dirname(filePath), ATOMIC_SIDECAR_DIRECTORY);
}

export const __test = {
  renameReplacingWithRetry,
};
