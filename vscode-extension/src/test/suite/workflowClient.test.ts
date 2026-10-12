import * as assert from "assert";
import * as fs from "fs/promises";
import * as path from "path";
import * as vm from "vm";

suite("workflow webview exclude editor", () => {
  test("preserves drafts across refreshes and closes only after a successful save", async () => {
    class Element {
      hidden = false;
      disabled = false;
      dataset = {};
      constructor(readonly id: string) {}
      closest() { return this; }
      focus() { document.activeElement = this; }
    }
    class TextArea extends Element {
      value = "build\nFreeCM\nthirdparty\nDownloads\nhi";
      selectionStart = 0;
      selectionEnd = 0;
      setSelectionRange(start: number, end: number) {
        this.selectionStart = start;
        this.selectionEnd = end;
      }
    }
    let click: (event: { target: Element }) => void = () => undefined;
    let message: (event: { data: unknown }) => void = () => undefined;
    const preview = new Element("countExcludePreview");
    const editor = new Element("countExcludeEditor");
    editor.hidden = true;
    const text = new TextArea("countExcludePathsText");
    const edit = new Element("editCountExcludePaths");
    const save = new Element("saveCountExcludePaths");
    const elements = new Map([preview, editor, text, edit, save].map((element) => [element.id, element]));
    const document = {
      activeElement: undefined as Element | undefined,
      getElementById: (id: string) => elements.get(id) ?? null,
      addEventListener: (_type: string, listener: typeof click) => { click = listener; },
    };
    const posted: unknown[] = [];
    const script = await fs.readFile(path.resolve(__dirname, "../../../resources/workflow.js"), "utf8");
    vm.runInNewContext(script, {
      acquireVsCodeApi: () => ({ postMessage: (value: unknown) => posted.push(value) }),
      Element, HTMLElement: Element, HTMLTextAreaElement: TextArea, document,
      window: { addEventListener: (_type: string, listener: typeof message) => { message = listener; } },
    });
    click({ target: edit });
    assert.strictEqual(editor.hidden, false);
    assert.strictEqual(preview.hidden, true);
    click({ target: save });
    assert.strictEqual(JSON.stringify(posted), JSON.stringify([
      { command: "saveCountExcludePaths", value: text.value },
    ]));
    // The normal state refresh used to reopen the saved editor.
    message({ data: { type: "workflowState", version: 1, regions: {} } });
    assert.strictEqual(editor.hidden, false, "refresh must preserve an unacknowledged draft");
    assert.strictEqual(text.value.endsWith("hi"), true);
    message({ data: { type: "codeCountExcludePathsSaved", version: 1 } });
    assert.strictEqual(editor.hidden, true);
    assert.strictEqual(preview.hidden, false);
    assert.strictEqual(document.activeElement, edit);
    message({ data: { type: "workflowState", version: 1, regions: {} } });
    assert.strictEqual(editor.hidden, true, "subsequent refresh must not reopen the editor");
    click({ target: edit });
    text.value = "*.invalid";
    click({ target: save });
    message({ data: { type: "workflowState", version: 1, regions: {} } });
    assert.strictEqual(editor.hidden, false, "without success acknowledgement invalid drafts stay editable");
    assert.strictEqual(text.value, "*.invalid");
  });
});
