import * as assert from "assert";
import { WorkspaceCache } from "../../workspaceCache";

suite("workspace cache", () => {
  test("returns cached entries until invalidated", () => {
    const cache = new WorkspaceCache<{ value: number }>();
    let creates = 0;
    const create = () => {
      creates += 1;
      return { value: creates };
    };

    assert.strictEqual(cache.getOrCreate("/repo", create).value, 1);
    assert.strictEqual(cache.getOrCreate("/repo", create).value, 1);
    assert.strictEqual(creates, 1);

    cache.delete("/repo");
    assert.strictEqual(cache.getOrCreate("/repo", create).value, 2);
    assert.strictEqual(creates, 2);

    cache.clear();
    assert.strictEqual(cache.getOrCreate("/repo", create).value, 3);
    assert.strictEqual(creates, 3);
  });
});
