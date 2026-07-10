import * as fs from "fs/promises";
import * as path from "path";

const ATOMIC_SIDECAR_DIRECTORY = path.join(".freecm", "atomic");

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
    await fs.rename(tempPath, filePath);
    await fsyncDirectory(directory);
  } catch (error) {
    await removeIfExists(tempPath);
    throw error;
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

function atomicSidecarDirectory(filePath: string): string {
  return path.join(path.dirname(filePath), ATOMIC_SIDECAR_DIRECTORY);
}
