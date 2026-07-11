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

  get(key: string): T | undefined {
    return this.entries.get(key);
  }

  set(key: string, value: T): void {
    this.entries.set(key, value);
  }

  keyValues(): IterableIterator<[string, T]> {
    return this.entries.entries();
  }

  values(): IterableIterator<T> {
    return this.entries.values();
  }

  clear(): void {
    this.entries.clear();
  }
}
