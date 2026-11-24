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

function selectTab(container, tab, focus) {
  container.querySelectorAll('[role="tab"]').forEach((item) => {
    const active = item === tab;
    item.setAttribute("aria-selected", String(active));
    item.tabIndex = active ? 0 : -1;
    const panel = document.getElementById(item.getAttribute("aria-controls"));
    if (panel) {
      panel.hidden = !active;
    }
  });
  const list = tab.parentElement;
  if (list) {
    const tabBox = tab.getBoundingClientRect();
    const listBox = list.getBoundingClientRect();
    if (tabBox.left < listBox.left) {
      list.scrollLeft -= listBox.left - tabBox.left + 16;
    } else if (tabBox.right > listBox.right) {
      list.scrollLeft += tabBox.right - listBox.right + 24;
    }
  }
  if (focus) {
    tab.focus();
  }
}

document.querySelectorAll("[data-tabs]").forEach((container) => {
  const tabs = Array.from(container.querySelectorAll('[role="tab"]'));
  if (!tabs.length) {
    return;
  }
  const fromHash = tabs.find((tab) => window.location.hash === `#${tab.dataset.tab}`);
  container.classList.add("tabs--ready");
  selectTab(container, fromHash || tabs[0], false);
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      selectTab(container, tab, false);
      history.replaceState(null, "", `#${tab.dataset.tab}`);
    });
  });
  container.querySelector('[role="tablist"]').addEventListener("keydown", (event) => {
    const index = tabs.indexOf(document.activeElement);
    if (index < 0) {
      return;
    }
    const moves = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: tabs.length - 1 };
    if (!(event.key in moves)) {
      return;
    }
    event.preventDefault();
    const next = tabs[(moves[event.key] + tabs.length) % tabs.length];
    selectTab(container, next, true);
    history.replaceState(null, "", `#${next.dataset.tab}`);
  });
});

window.addEventListener("hashchange", () => {
  const key = window.location.hash.slice(1);
  document.querySelectorAll("[data-tabs]").forEach((container) => {
    const tab = container.querySelector(`[role="tab"][data-tab="${CSS.escape(key)}"]`);
    if (tab) {
      selectTab(container, tab, false);
    }
  });
});
