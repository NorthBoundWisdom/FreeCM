const vscode = acquireVsCodeApi();

function postCommand(command) {
  vscode.postMessage({ command });
}

function postElementCommand(element) {
  const command = element.dataset.command;
  if (command === undefined) {
    return;
  }
  const dependency = element.dataset.dependency;
  if (dependency === undefined) {
    postCommand(command);
    return;
  }
  vscode.postMessage({ command, dependency });
}

function bindButton(id, command) {
  const element = document.getElementById(id);
  if (element === null) {
    return;
  }
  element.addEventListener("click", () => {
    postCommand(command);
  });
}

bindButton("pull", "pull");
bindButton("pullFreeCM", "pullFreeCM");
bindButton("init", "init");
bindButton("update", "update");
bindButton("usePinned", "usePinned");
bindButton("pinLatest", "pinLatest");
bindButton("manualAll", "manualAll");
bindButton("updateUsed", "updateUsed");
bindButton("cleanBuild", "cleanBuild");
bindButton("countCode", "countCode");
bindButton("changeCountPath", "changeCountPath");
bindButton("resetCountPath", "resetCountPath");

const excludePreview = document.getElementById("countExcludePreview");
const excludeEditor = document.getElementById("countExcludeEditor");
const excludeText = document.getElementById("countExcludePathsText");

function setExcludeEditorVisible(visible) {
  if (excludePreview === null || excludeEditor === null) {
    return;
  }
  excludePreview.hidden = visible;
  excludeEditor.hidden = !visible;
  if (visible && excludeText !== null) {
    excludeText.focus();
  }
}

const editExcludeButton = document.getElementById("editCountExcludePaths");
if (editExcludeButton !== null) {
  editExcludeButton.addEventListener("click", () => {
    setExcludeEditorVisible(true);
  });
}

const cancelExcludeButton = document.getElementById("cancelCountExcludePaths");
if (cancelExcludeButton !== null) {
  cancelExcludeButton.addEventListener("click", () => {
    setExcludeEditorVisible(false);
  });
}

const saveExcludeButton = document.getElementById("saveCountExcludePaths");
if (saveExcludeButton !== null && excludeText !== null) {
  saveExcludeButton.addEventListener("click", () => {
    vscode.postMessage({
      command: "saveCountExcludePaths",
      value: excludeText.value,
    });
  });
}

document.querySelectorAll("[data-command]").forEach((element) => {
  element.addEventListener("click", () => {
    postElementCommand(element);
  });
});
