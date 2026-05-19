export class WorkspaceCache<T> {
  private readonly entries = new Map<string, T>();

  getOrCreate(key: string, create: () => T): T {
    let entry = this.entries.get(key);
    if (entry === undefined) {
      entry = create();
      this.entries.set(key, entry);
    }
    return entry;
  }

  delete(key: string): void {
    this.entries.delete(key);
  }

  clear(): void {
    this.entries.clear();
  }
}
