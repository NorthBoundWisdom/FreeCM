import { AsyncLocalStorage } from "async_hooks";
import { performance } from "perf_hooks";

export interface ExtensionPerformanceReport {
  readonly name: string;
  readonly filesystemReads: number;
  readonly spawnedGitProcesses: number;
  readonly peakConcurrentReads: number;
  readonly peakConcurrentGitProcesses: number;
  readonly durationMs: number;
}

interface PerformanceRecorder {
  filesystemReads: number;
  spawnedGitProcesses: number;
  concurrentReads: number;
  peakConcurrentReads: number;
  concurrentGitProcesses: number;
  peakConcurrentGitProcesses: number;
}

const activeRecorder = new AsyncLocalStorage<PerformanceRecorder>();

export async function captureExtensionPerformance<T>(
  name: string,
  action: () => Promise<T>,
): Promise<{ readonly result: T; readonly report: ExtensionPerformanceReport }> {
  const recorder: PerformanceRecorder = {
    filesystemReads: 0,
    spawnedGitProcesses: 0,
    concurrentReads: 0,
    peakConcurrentReads: 0,
    concurrentGitProcesses: 0,
    peakConcurrentGitProcesses: 0,
  };
  const start = performance.now();
  const result = await activeRecorder.run(recorder, action);
  return {
    result,
    report: {
      name,
      filesystemReads: recorder.filesystemReads,
      spawnedGitProcesses: recorder.spawnedGitProcesses,
      peakConcurrentReads: recorder.peakConcurrentReads,
      peakConcurrentGitProcesses: recorder.peakConcurrentGitProcesses,
      durationMs: performance.now() - start,
    },
  };
}

export function beginFilesystemRead(): () => void {
  const recorder = activeRecorder.getStore();
  if (recorder === undefined) {
    return () => undefined;
  }
  recorder.filesystemReads += 1;
  recorder.concurrentReads += 1;
  recorder.peakConcurrentReads = Math.max(
    recorder.peakConcurrentReads,
    recorder.concurrentReads,
  );
  let finished = false;
  return () => {
    if (finished) {
      return;
    }
    finished = true;
    recorder.concurrentReads -= 1;
  };
}

export function beginGitProcess(): () => void {
  const recorder = activeRecorder.getStore();
  if (recorder === undefined) {
    return () => undefined;
  }
  recorder.spawnedGitProcesses += 1;
  recorder.concurrentGitProcesses += 1;
  recorder.peakConcurrentGitProcesses = Math.max(
    recorder.peakConcurrentGitProcesses,
    recorder.concurrentGitProcesses,
  );
  let finished = false;
  return () => {
    if (finished) {
      return;
    }
    finished = true;
    recorder.concurrentGitProcesses -= 1;
  };
}
