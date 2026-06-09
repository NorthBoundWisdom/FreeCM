import * as fs from "fs/promises";
import * as path from "path";

export interface AtomicWriteOptions {
  readonly lockTimeoutMs?: number;
  readonly retryDelayMs?: number;
}

const DEFAULT_LOCK_TIMEOUT_MS = 5000;
const DEFAULT_RETRY_DELAY_MS = 25;
const ATOMIC_SIDECAR_DIRECTORY = path.join(".freecm", "atomic");

export async function atomicWriteText(
  filePath: string,
  text: string,
  options: AtomicWriteOptions = {},
): Promise<void> {
  await withWriteLock(filePath, options, async () => {
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
      const handle = await fs.open(tempPath, "wx");
      try {
        await handle.writeFile(text, "utf8");
        await handle.sync();
      } finally {
        await handle.close();
      }
      await fs.rename(tempPath, filePath);
      await fsyncDirectory(directory);
    } catch (error) {
      await removeIfExists(tempPath);
      throw error;
    }
  });
}

export async function withWriteLock<T>(
  filePath: string,
  options: AtomicWriteOptions,
  operation: () => Promise<T>,
): Promise<T> {
  return withLockPath(vscodeLockPath(filePath), options, operation);
}

export async function withLockPath<T>(
  lockPath: string,
  options: AtomicWriteOptions,
  operation: () => Promise<T>,
): Promise<T> {
  await acquireLock(lockPath, options);
  try {
    return await operation();
  } finally {
    await fs.rm(lockPath, { force: true, recursive: true });
  }
}

async function acquireLock(
  lockPath: string,
  options: AtomicWriteOptions,
): Promise<void> {
  const timeoutMs = options.lockTimeoutMs ?? DEFAULT_LOCK_TIMEOUT_MS;
  const retryDelayMs = options.retryDelayMs ?? DEFAULT_RETRY_DELAY_MS;
  const deadline = Date.now() + timeoutMs;

  await fs.mkdir(path.dirname(lockPath), { recursive: true });
  for (;;) {
    try {
      await fs.mkdir(lockPath);
      return;
    } catch (error) {
      if (!isNodeErrorCode(error, "EEXIST") || Date.now() >= deadline) {
        throw new Error(`Unable to acquire lock ${lockPath}: ${errorMessage(error)}`);
      }
      await delay(retryDelayMs);
    }
  }
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

function vscodeLockPath(filePath: string): string {
  const baseName = path.basename(filePath);
  return path.join(atomicSidecarDirectory(filePath), `.${baseName}.vscode.lock`);
}

function atomicSidecarDirectory(filePath: string): string {
  return path.join(path.dirname(filePath), ATOMIC_SIDECAR_DIRECTORY);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isNodeErrorCode(error: unknown, code: string): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { code?: unknown }).code === code
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
