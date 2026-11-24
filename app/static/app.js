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

function markScrollableTables(root = document) {
  root.querySelectorAll(".table-scroll").forEach((wrapper) => {
    if (wrapper.scrollWidth > wrapper.clientWidth + 1) {
      wrapper.tabIndex = 0;
      wrapper.setAttribute("role", "region");
      wrapper.setAttribute("aria-label", "Tableau défilant");
    } else {
      wrapper.removeAttribute("tabindex");
      wrapper.removeAttribute("role");
      wrapper.removeAttribute("aria-label");
    }
  });
}

markScrollableTables();
window.addEventListener("resize", () => markScrollableTables());

function openViewer(dialog) {
  const image = dialog.querySelector("img[data-src]");
  if (image && !image.getAttribute("src")) {
    image.src = image.dataset.src;
  }
  dialog.classList.remove("is-zoomed");
  dialog.querySelectorAll("[data-viewer-zoom]").forEach((button) => button.setAttribute("aria-pressed", "false"));
  dialog.showModal();
}

document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) {
    return;
  }
  const opener = target.closest("[data-viewer-open]");
  if (opener) {
    const dialog = document.getElementById(opener.getAttribute("data-viewer-open"));
    if (dialog instanceof HTMLDialogElement && typeof dialog.showModal === "function") {
      event.preventDefault();
      openViewer(dialog);
    }
    return;
  }
  const dialog = target.closest("dialog.viewer");
  if (!(dialog instanceof HTMLDialogElement)) {
    return;
  }
  if (target.closest("[data-viewer-close]") || target === dialog) {
    dialog.close();
    return;
  }
  const zoom = target.closest("[data-viewer-zoom]");
  if (zoom) {
    const zoomed = dialog.classList.toggle("is-zoomed");
    zoom.setAttribute("aria-pressed", String(zoomed));
  }
});
