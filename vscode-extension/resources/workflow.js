const vscode = acquireVsCodeApi();

const commandById = Object.freeze({
  pull: "pull",
  pullSeeds: "pullSeeds",
  init: "init",
  update: "update",
  usePinned: "usePinned",
  pinLatest: "pinLatest",
  manualAll: "manualAll",
  updateUsed: "updateUsed",
  cleanBuild: "cleanBuild",
  countCode: "countCode",
  changeCountPath: "changeCountPath",
  resetCountPath: "resetCountPath",
});

const workflowRegionIds = new Set([
  "freecm-target",
  "freecm-workflow",
  "freecm-dependencies",
  "freecm-active-lock",
  "freecm-maintenance",
  "freecm-repo-commands",
  "freecm-code-count",
]);

function postElementCommand(element) {
  const command = element.dataset.command ?? commandById[element.id];
  if (command === undefined) {
    return;
  }
  const dependency = element.dataset.dependency;
  vscode.postMessage(
    dependency === undefined ? { command } : { command, dependency },
  );
}

function setExcludeEditorVisible(visible) {
  const preview = document.getElementById("countExcludePreview");
  const editor = document.getElementById("countExcludeEditor");
  const text = document.getElementById("countExcludePathsText");
  if (preview === null || editor === null) {
    return;
  }
  preview.hidden = visible;
  editor.hidden = !visible;
  if (visible && text !== null) {
    text.focus();
  }
}

document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) {
    return;
  }
  const element = event.target.closest("button");
  if (element === null || element.disabled) {
    return;
  }
  if (element.id === "editCountExcludePaths") {
    setExcludeEditorVisible(true);
    return;
  }
  if (element.id === "cancelCountExcludePaths") {
    setExcludeEditorVisible(false);
    return;
  }
  if (element.id === "saveCountExcludePaths") {
    const text = document.getElementById("countExcludePathsText");
    if (text instanceof HTMLTextAreaElement) {
      vscode.postMessage({
        command: "saveCountExcludePaths",
        value: text.value,
      });
    }
    return;
  }
  postElementCommand(element);
});

function captureEditorState() {
  const preview = document.getElementById("countExcludePreview");
  const editor = document.getElementById("countExcludeEditor");
  const text = document.getElementById("countExcludePathsText");
  const active = document.activeElement;
  return {
    editorVisible: editor !== null && !editor.hidden,
    previewHidden: preview?.hidden ?? false,
    value: text instanceof HTMLTextAreaElement ? text.value : undefined,
    activeId: active instanceof HTMLElement ? active.id : undefined,
    selectionStart:
      active instanceof HTMLTextAreaElement ? active.selectionStart : undefined,
    selectionEnd:
      active instanceof HTMLTextAreaElement ? active.selectionEnd : undefined,
  };
}

function restoreEditorState(state) {
  if (state.editorVisible) {
    const preview = document.getElementById("countExcludePreview");
    const editor = document.getElementById("countExcludeEditor");
    const text = document.getElementById("countExcludePathsText");
    if (preview !== null) {
      preview.hidden = state.previewHidden;
    }
    if (editor !== null) {
      editor.hidden = false;
    }
    if (text instanceof HTMLTextAreaElement && state.value !== undefined) {
      text.value = state.value;
    }
  }
  if (state.activeId === undefined || state.activeId === "") {
    return;
  }
  const active = document.getElementById(state.activeId);
  if (active instanceof HTMLElement) {
    active.focus();
  }
  if (
    active instanceof HTMLTextAreaElement &&
    state.selectionStart !== undefined &&
    state.selectionEnd !== undefined
  ) {
    active.setSelectionRange(state.selectionStart, state.selectionEnd);
  }
}

window.addEventListener("message", (event) => {
  const message = event.data;
  if (
    message === null ||
    typeof message !== "object" ||
    message.type !== "workflowState" ||
    message.version !== 1 ||
    message.regions === null ||
    typeof message.regions !== "object"
  ) {
    return;
  }
  const editorState = captureEditorState();
  for (const [id, html] of Object.entries(message.regions)) {
    if (!workflowRegionIds.has(id) || typeof html !== "string") {
      continue;
    }
    const current = document.getElementById(id);
    if (current !== null && current.outerHTML !== html) {
      current.outerHTML = html;
    }
  }
  restoreEditorState(editorState);
});
