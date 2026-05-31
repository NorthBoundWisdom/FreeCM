const vscode = acquireVsCodeApi();

function postCommand(command) {
  vscode.postMessage({ command });
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
bindButton("addCountExcludeFolder", "addCountExcludeFolder");
bindButton("removeCountExcludeFolder", "removeCountExcludeFolder");

document.querySelectorAll("[data-command]").forEach((element) => {
  element.addEventListener("click", () => {
    postCommand(element.dataset.command);
  });
});
