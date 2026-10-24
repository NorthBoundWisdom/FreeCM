import * as fs from "fs";
import * as os from "os";
import * as path from "path";

export function resolveDependencyPath(repoRoot: string, value: string): string {
  let expanded = value.replace(/\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g,
    (match, braced: string | undefined, plain: string | undefined) =>
      process.env[braced ?? plain ?? ""] ?? match);
  if (process.platform === "win32") {
    expanded = expanded.replace(/%([^%]+)%/g,
      (match, name: string) => process.env[name] ?? match);
  }
  if (expanded === "~" || expanded.startsWith("~/") || expanded.startsWith("~\\")) {
    expanded = path.join(os.homedir(), expanded.slice(1));
  }
  return path.resolve(repoRoot, expanded);
}

export function realDependencyPath(value: string): string {
  try {
    return fs.realpathSync.native(value);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    const parent = path.dirname(value);
    return parent === value ? value : path.join(realDependencyPath(parent), path.basename(value));
  }
}

export function isInsideDependencyPath(candidate: string, parent: string): boolean {
  const relative = path.relative(parent, candidate);
  return relative === "" || (!path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`));
}
