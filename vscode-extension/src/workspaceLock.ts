import { AsyncLocalStorage } from "async_hooks";
import { spawn } from "child_process";
import * as crypto from "crypto";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import {
  WORKSPACE_LOCK_NAME,
  WORKSPACE_LOCK_PROTOCOL,
} from "./lockSchema";

export interface WorkspaceLockOptions {
  readonly timeoutMs?: number;
  readonly retryDelayMs?: number;
  readonly initializationGraceMs?: number;
}

interface WorkspaceLockOwner {
  readonly schemaVersion: number;
  readonly token: string;
  readonly pid: number;
  readonly processStartToken: string | null;
  readonly hostname: string;
  readonly implementation: string;
  readonly acquiredAt: string;
}

interface HeldWorkspaceLock {
  readonly owner: WorkspaceLockOwner;
  depth: number;
}

interface WorkspaceLockContext {
  readonly locks: Map<string, HeldWorkspaceLock>;
}

type ProcessState = "live" | "dead" | "unknown";

interface ProcessIdentity {
  readonly state: ProcessState;
  readonly startToken: string | null;
}

const RECLAIM_CLAIM_FILE_NAME = ".reclaim";
const ABANDONED_MARKER_PREFIX = ".abandoned.";
const OWNER_PROBE_INTERVAL_MS = 250;
const lockContext = new AsyncLocalStorage<WorkspaceLockContext>();
let currentProcessStartToken: Promise<string | null> | undefined;

export async function withWorkspaceLock<T>(
  repoRoot: string,
  operation: () => Promise<T>,
  options: WorkspaceLockOptions = {},
): Promise<T> {
  const realRepoRoot = await fs.realpath(repoRoot);
  const lockPath = path.join(realRepoRoot, WORKSPACE_LOCK_NAME);
  const currentContext = lockContext.getStore();
  const held = currentContext?.locks.get(lockPath);
  if (held !== undefined) {
    held.depth += 1;
    try {
      return await operation();
    } finally {
      held.depth -= 1;
    }
  }

  const owner = await acquireWorkspaceLock(lockPath, options);
  const context = { locks: new Map(currentContext?.locks ?? []) };
  context.locks.set(lockPath, { owner, depth: 1 });
  try {
    return await lockContext.run(context, operation);
  } finally {
    context.locks.delete(lockPath);
    await releaseWorkspaceLock(lockPath, owner.token);
  }
}

async function acquireWorkspaceLock(
  lockPath: string,
  options: WorkspaceLockOptions,
): Promise<WorkspaceLockOwner> {
  const timeoutMs = options.timeoutMs ?? WORKSPACE_LOCK_PROTOCOL.timeoutMs;
  const retryDelayMs =
    options.retryDelayMs ?? WORKSPACE_LOCK_PROTOCOL.retryDelayMs;
  const initializationGraceMs =
    options.initializationGraceMs ??
    WORKSPACE_LOCK_PROTOCOL.initializationGraceMs;
  const deadline = Date.now() + timeoutMs;
  const owner = await newOwner();
  const ownerProbeCache = new Map<
    string,
    { readonly checkedAt: number; readonly stale: boolean }
  >();

  await fs.mkdir(path.dirname(lockPath), { recursive: true });
  for (;;) {
    try {
      await fs.mkdir(lockPath);
    } catch (error) {
      if (!isNodeErrorCode(error, "EEXIST")) {
        throw new Error(
          `Unable to acquire workspace lock ${lockPath}: ${errorMessage(error)}`,
        );
      }
      const observedOwner = await readOwner(lockPath);
      const observedIdentity = await pathIdentity(lockPath);
      const stale =
        observedOwner !== undefined &&
        ((await ownerIsAbandoned(lockPath, observedOwner)) ||
          (await ownerIsStaleThrottled(observedOwner, ownerProbeCache)));
      const invalidMature =
        observedOwner === undefined &&
        observedIdentity !== undefined &&
        (await lockAgeMs(lockPath)) >= initializationGraceMs;
      if (
        (stale || invalidMature) &&
        (await tryReclaimLock(
          lockPath,
          observedOwner,
          observedIdentity,
          invalidMature,
        ))
      ) {
        continue;
      }
      if (Date.now() >= deadline) {
        throw new Error(
          `Unable to acquire workspace lock: ${lockPath}; current owner: ${formatOwner(observedOwner)}`,
        );
      }
      await delay(Math.max(0, Math.min(retryDelayMs, deadline - Date.now())));
      continue;
    }

    await writeOwner(lockPath, owner);
    if (!(await confirmNewOwner(lockPath, owner, deadline, retryDelayMs))) {
      continue;
    }
    return owner;
  }
}

async function releaseWorkspaceLock(
  lockPath: string,
  token: string,
): Promise<void> {
  const owner = await readOwner(lockPath);
  if (owner === undefined || owner.token !== token) {
    throw new Error(
      `Workspace lock ownership changed before release: ${lockPath}; current owner: ${formatOwner(owner)}`,
    );
  }
  const tombstone = `${lockPath}.released.${token}`;
  try {
    await fs.rename(lockPath, tombstone);
  } catch (error) {
    throw new Error(
      `Workspace lock disappeared before release: ${lockPath}: ${errorMessage(error)}`,
    );
  }
  await fs.rm(tombstone, { recursive: true, force: true });
}

async function newOwner(): Promise<WorkspaceLockOwner> {
  return {
    schemaVersion: WORKSPACE_LOCK_PROTOCOL.schemaVersion,
    token: crypto.randomBytes(16).toString("hex"),
    pid: process.pid,
    processStartToken: await currentProcessToken(),
    hostname: normalizedHostname(),
    implementation: "vscode",
    acquiredAt: new Date().toISOString(),
  };
}

async function currentProcessToken(): Promise<string | null> {
  currentProcessStartToken ??= queryProcessIdentity(process.pid).then(
    (identity) => identity.startToken,
  );
  return await currentProcessStartToken;
}

async function writeOwner(
  lockPath: string,
  owner: WorkspaceLockOwner,
): Promise<void> {
  await writeOwnerPath(
    path.join(lockPath, WORKSPACE_LOCK_PROTOCOL.ownerFileName),
    owner,
  );
}

async function writeOwnerPath(
  ownerPath: string,
  owner: WorkspaceLockOwner,
): Promise<void> {
  const handle = await fs.open(ownerPath, "wx");
  try {
    await handle.writeFile(`${JSON.stringify(owner)}\n`, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function confirmNewOwner(
  lockPath: string,
  owner: WorkspaceLockOwner,
  deadline: number,
  retryDelayMs: number,
): Promise<boolean> {
  const claimPath = path.join(lockPath, RECLAIM_CLAIM_FILE_NAME);
  for (;;) {
    const currentOwner = await readOwner(lockPath);
    if (currentOwner?.token !== owner.token) {
      return false;
    }
    if (!(await pathExists(claimPath))) {
      return true;
    }
    if (Date.now() >= deadline) {
      const claimOwner = await readOwnerPath(claimPath);
      await markNewOwnerAbandoned(lockPath, owner);
      throw new Error(
        `Unable to acquire workspace lock: ${lockPath}; active reclaimer: ${formatReclaimer(claimOwner)}`,
      );
    }
    await delay(Math.max(0, Math.min(retryDelayMs, deadline - Date.now())));
  }
}

async function markNewOwnerAbandoned(
  lockPath: string,
  owner: WorkspaceLockOwner,
): Promise<void> {
  const markerPath = abandonedMarkerPath(lockPath, owner.token);
  try {
    const handle = await fs.open(markerPath, "wx");
    try {
      await handle.sync();
    } finally {
      await handle.close();
    }
  } catch {
    // The marker already exists or the reclaimer moved this generation.
  }
}

async function ownerIsAbandoned(
  lockPath: string,
  owner: WorkspaceLockOwner,
): Promise<boolean> {
  return pathExists(abandonedMarkerPath(lockPath, owner.token));
}

function abandonedMarkerPath(lockPath: string, ownerToken: string): string {
  const tokenDigest = crypto
    .createHash("sha256")
    .update(ownerToken, "utf8")
    .digest("hex");
  return path.join(lockPath, `${ABANDONED_MARKER_PREFIX}${tokenDigest}`);
}

async function readOwner(
  lockPath: string,
): Promise<WorkspaceLockOwner | undefined> {
  return readOwnerPath(
    path.join(lockPath, WORKSPACE_LOCK_PROTOCOL.ownerFileName),
  );
}

async function readOwnerPath(
  ownerPath: string,
): Promise<WorkspaceLockOwner | undefined> {
  try {
    return parseOwner(
      JSON.parse(
        await fs.readFile(ownerPath, "utf8"),
      ) as unknown,
    );
  } catch {
    return undefined;
  }
}

function parseOwner(value: unknown): WorkspaceLockOwner | undefined {
  if (!isObject(value)) {
    return undefined;
  }
  const processStartToken = value.processStartToken;
  if (
    value.schemaVersion !== WORKSPACE_LOCK_PROTOCOL.schemaVersion ||
    typeof value.token !== "string" ||
    value.token.length === 0 ||
    typeof value.pid !== "number" ||
    !Number.isInteger(value.pid) ||
    value.pid <= 0 ||
    (processStartToken !== null &&
      (typeof processStartToken !== "string" || processStartToken.length === 0)) ||
    typeof value.hostname !== "string" ||
    value.hostname.length === 0 ||
    typeof value.implementation !== "string" ||
    value.implementation.length === 0 ||
    typeof value.acquiredAt !== "string" ||
    value.acquiredAt.length === 0
  ) {
    return undefined;
  }
  return {
    schemaVersion: value.schemaVersion,
    token: value.token,
    pid: value.pid,
    processStartToken,
    hostname: value.hostname,
    implementation: value.implementation,
    acquiredAt: value.acquiredAt,
  };
}

async function ownerIsStale(owner: WorkspaceLockOwner): Promise<boolean> {
  if (owner.hostname !== normalizedHostname()) {
    return false;
  }
  if (owner.pid === process.pid) {
    if (owner.processStartToken === null) {
      return false;
    }
    const currentToken = await currentProcessToken();
    return currentToken !== null && owner.processStartToken !== currentToken;
  }
  const identity = await queryProcessIdentity(owner.pid);
  if (identity.state === "dead") {
    return true;
  }
  return (
    identity.state === "live" &&
    owner.processStartToken !== null &&
    identity.startToken !== null &&
    owner.processStartToken !== identity.startToken
  );
}

async function ownerIsStaleThrottled(
  owner: WorkspaceLockOwner,
  cache: Map<string, { readonly checkedAt: number; readonly stale: boolean }>,
): Promise<boolean> {
  const now = Date.now();
  const cached = cache.get(owner.token);
  if (cached !== undefined && now - cached.checkedAt < OWNER_PROBE_INTERVAL_MS) {
    return cached.stale;
  }
  const stale = await ownerIsStale(owner);
  cache.clear();
  cache.set(owner.token, { checkedAt: now, stale });
  return stale;
}

async function tryReclaimLock(
  lockPath: string,
  observedOwner: WorkspaceLockOwner | undefined,
  observedIdentity: string | undefined,
  invalidMature: boolean,
): Promise<boolean> {
  const claimOwner = await acquireReclaimClaim(lockPath);
  if (claimOwner === undefined) {
    return false;
  }
  const claimPath = path.join(lockPath, RECLAIM_CLAIM_FILE_NAME);

  try {
    const currentOwner = await readOwner(lockPath);
    const sameGeneration =
      observedIdentity !== undefined &&
      (await pathIdentity(lockPath)) === observedIdentity;
    const shouldReclaim =
      observedOwner === undefined
        ? sameGeneration &&
          ((invalidMature && currentOwner === undefined) ||
            (currentOwner !== undefined &&
              (await ownerIsAbandoned(lockPath, currentOwner))))
        : currentOwner !== undefined &&
          currentOwner.token === observedOwner.token &&
          ((await ownerIsAbandoned(lockPath, currentOwner)) ||
            (await ownerIsStale(currentOwner)));
    if (!shouldReclaim) {
      return false;
    }
    if ((await readOwnerPath(claimPath))?.token !== claimOwner.token) {
      return false;
    }
    const tombstone = `${lockPath}.stale.${claimOwner.token}`;
    try {
      await fs.rename(lockPath, tombstone);
    } catch {
      return false;
    }
    await fs.rm(tombstone, { recursive: true, force: true });
    return true;
  } finally {
    await removeClaimIfOwned(claimPath, claimOwner.token);
  }
}

async function acquireReclaimClaim(
  lockPath: string,
): Promise<WorkspaceLockOwner | undefined> {
  const claimPath = path.join(lockPath, RECLAIM_CLAIM_FILE_NAME);
  const claimOwner = await newOwner();
  try {
    await publishOwnerPath(claimPath, claimOwner);
    return claimOwner;
  } catch (error) {
    if (!isNodeErrorCode(error, "EEXIST")) {
      return undefined;
    }
  }

  const observedOwner = await readOwnerPath(claimPath);
  const stale =
    observedOwner !== undefined && (await ownerIsStale(observedOwner));
  if (observedOwner !== undefined && stale) {
    await removeStaleClaim(claimPath, observedOwner);
  }
  return undefined;
}

async function removeStaleClaim(
  claimPath: string,
  observedOwner: WorkspaceLockOwner,
): Promise<void> {
  const currentOwner = await readOwnerPath(claimPath);
  const removable =
    currentOwner !== undefined &&
    currentOwner.token === observedOwner.token &&
    (await ownerIsStale(currentOwner));
  if (!removable) {
    return;
  }
  const tombstone = `${claimPath}.stale.${crypto.randomBytes(16).toString("hex")}`;
  try {
    await fs.rename(claimPath, tombstone);
  } catch {
    return;
  }
  const movedOwner = await readOwnerPath(tombstone);
  if (movedOwner?.token === observedOwner.token) {
    await fs.rm(tombstone, { force: true });
    return;
  }
  try {
    await fs.rename(tombstone, claimPath);
  } catch {
    // A current claimant already occupies the canonical claim path.
  }
}

async function publishOwnerPath(
  ownerPath: string,
  owner: WorkspaceLockOwner,
): Promise<void> {
  const candidatePath = `${ownerPath}.candidate.${owner.token}`;
  try {
    await writeOwnerPath(candidatePath, owner);
    await fs.link(candidatePath, ownerPath);
  } finally {
    try {
      await fs.rm(candidatePath, { force: true });
    } catch {
      // A moved or already-cleaned candidate does not invalidate publication.
    }
  }
}

async function removeClaimIfOwned(
  claimPath: string,
  claimToken: string,
): Promise<void> {
  try {
    if ((await readOwnerPath(claimPath))?.token === claimToken) {
      await fs.rm(claimPath, { force: true });
    }
  } catch {
    // Another process reclaimed or removed the lock directory.
  }
}

async function lockAgeMs(lockPath: string): Promise<number> {
  return pathAgeMs(lockPath);
}

async function pathAgeMs(filePath: string): Promise<number> {
  try {
    const stat = await fs.stat(filePath);
    return Math.max(0, Date.now() - stat.mtimeMs);
  } catch {
    return 0;
  }
}

async function pathExists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function pathIdentity(filePath: string): Promise<string | undefined> {
  try {
    const stat = await fs.stat(filePath);
    return `${stat.dev}:${stat.ino}`;
  } catch {
    return undefined;
  }
}

async function queryProcessIdentity(pid: number): Promise<ProcessIdentity> {
  if (!Number.isInteger(pid) || pid <= 0) {
    return { state: "dead", startToken: null };
  }
  if (process.platform === "linux") {
    return linuxProcessIdentity(pid);
  }
  if (process.platform === "darwin") {
    return darwinProcessIdentity(pid);
  }
  if (process.platform === "win32") {
    return windowsProcessIdentity(pid);
  }
  return processLiveness(pid);
}

async function linuxProcessIdentity(pid: number): Promise<ProcessIdentity> {
  let statText: string;
  try {
    statText = await fs.readFile(`/proc/${pid}/stat`, "utf8");
  } catch (error) {
    return isNodeErrorCode(error, "ENOENT")
      ? { state: "dead", startToken: null }
      : { state: "unknown", startToken: null };
  }
  const closingParenthesis = statText.lastIndexOf(")");
  const fields = statText.slice(closingParenthesis + 1).trim().split(/\s+/);
  if (closingParenthesis < 0 || fields.length <= 19) {
    return { state: "unknown", startToken: null };
  }
  try {
    const bootId = (
      await fs.readFile("/proc/sys/kernel/random/boot_id", "utf8")
    ).trim();
    return {
      state: "live",
      startToken: bootId.length > 0 ? `linux:${bootId}:${fields[19]}` : null,
    };
  } catch {
    return { state: "live", startToken: null };
  }
}

async function darwinProcessIdentity(pid: number): Promise<ProcessIdentity> {
  const liveness = processLiveness(pid);
  if (liveness.state !== "live") {
    return liveness;
  }
  const result = await runCommand(
    "ps",
    ["-p", String(pid), "-o", "lstart="],
    { ...process.env, LC_ALL: "C" },
  );
  const token = result.stdout.trim();
  return {
    state: "live",
    startToken:
      result.exitCode === 0 && token.length > 0 ? `darwin:${token}` : null,
  };
}

async function windowsProcessIdentity(pid: number): Promise<ProcessIdentity> {
  const liveness = processLiveness(pid);
  if (liveness.state !== "live") {
    return liveness;
  }
  const script =
    `$p = Get-Process -Id ${pid} -ErrorAction Stop; ` +
    "[Console]::Write($p.StartTime.ToUniversalTime().ToFileTimeUtc())";
  const result = await runCommand(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-Command", script],
    process.env,
  );
  const token = result.stdout.trim();
  return {
    state: "live",
    startToken:
      result.exitCode === 0 && /^\d+$/.test(token)
        ? `windows:${token}`
        : null,
  };
}

function processLiveness(pid: number): ProcessIdentity {
  try {
    process.kill(pid, 0);
    return { state: "live", startToken: null };
  } catch (error) {
    if (isNodeErrorCode(error, "ESRCH")) {
      return { state: "dead", startToken: null };
    }
    return { state: "unknown", startToken: null };
  }
}

async function runCommand(
  command: string,
  args: readonly string[],
  env: NodeJS.ProcessEnv,
): Promise<{ readonly exitCode: number | null; readonly stdout: string }> {
  return new Promise((resolve) => {
    const child = spawn(command, [...args], {
      env,
      shell: false,
      stdio: ["ignore", "pipe", "ignore"],
      windowsHide: true,
    });
    let stdout = "";
    let settled = false;
    const finish = (exitCode: number | null): void => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timeout);
      resolve({ exitCode, stdout });
    };
    child.stdout.on("data", (chunk: Buffer | string) => {
      if (stdout.length < 4096) {
        stdout += chunk.toString().slice(0, 4096 - stdout.length);
      }
    });
    child.on("error", () => finish(null));
    child.on("close", (exitCode) => finish(exitCode));
    const timeout = setTimeout(() => {
      child.kill();
      finish(null);
    }, 2000);
  });
}

function normalizedHostname(): string {
  return os.hostname().trim().toLowerCase();
}

function formatOwner(owner: WorkspaceLockOwner | undefined): string {
  if (owner === undefined) {
    return "missing or invalid owner metadata";
  }
  return (
    `pid=${owner.pid}, hostname=${owner.hostname}, ` +
    `processStartToken=${owner.processStartToken ?? "<unknown>"}, ` +
    `implementation=${owner.implementation}, acquiredAt=${owner.acquiredAt}`
  );
}

function formatReclaimer(owner: WorkspaceLockOwner | undefined): string {
  return owner === undefined ? "invalid reclaimer metadata" : formatOwner(owner);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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
