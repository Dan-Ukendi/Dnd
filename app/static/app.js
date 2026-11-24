if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  });
}

document.addEventListener("focusin", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.classList.contains("copy-field")) {
    target.select();
  }
});

const desktop = window.matchMedia("(min-width: 64rem)");

function syncPanels(root = document) {
  root.querySelectorAll("details[data-responsive-panel]").forEach((panel) => {
    panel.open = desktop.matches;
  });
}

syncPanels();
desktop.addEventListener("change", () => syncPanels());

document.addEventListener("click", (event) => {
  const summary = event.target instanceof Element ? event.target.closest("summary") : null;
  if (summary && desktop.matches && summary.parentElement?.hasAttribute("data-responsive-panel")) {
    event.preventDefault();
  }
});
