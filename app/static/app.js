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

document.addEventListener("submit", (event) => {
  const message = event.target.dataset ? event.target.dataset.confirm : undefined;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

const HEX_COLOR = /^#[0-9a-f]{6}$/i;
const GEOMETRY = { x: "--x", w: "--w", lane: "--lane", ranges: "--range-lanes", lanes: "--point-lanes" };

function applyGeometry(root = document) {
  const scope = root instanceof Element || root instanceof Document ? root : document;
  const nodes = scope.querySelectorAll("[data-x], [data-w], [data-lane], [data-ranges], [data-lanes], [data-color]");
  const targets = scope instanceof Element ? [scope, ...nodes] : nodes;
  targets.forEach((node) => {
    Object.entries(GEOMETRY).forEach(([key, property]) => {
      const raw = node.dataset[key];
      if (raw === undefined) {
        return;
      }
      const value = Number.parseFloat(raw);
      if (Number.isFinite(value)) {
        node.style.setProperty(property, key === "x" || key === "w" ? `${value}px` : String(value));
      }
    });
    const color = node.dataset.color;
    if (color && HEX_COLOR.test(color)) {
      node.style.setProperty("--era", color);
    }
  });
}

const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function scrollFriseTo(viewport, x, center) {
  const left = center ? x - viewport.clientWidth / 2 : x - 16;
  viewport.scrollTo({ left: Math.max(0, left), behavior: prefersReducedMotion.matches ? "auto" : "smooth" });
}

function initFrise(root = document) {
  root.querySelectorAll("[data-frise]").forEach((frise) => {
    const viewport = frise.querySelector("[data-frise-viewport]");
    if (!viewport || frise.dataset.ready) {
      return;
    }
    frise.dataset.ready = "true";
    const selected = viewport.querySelector(".is-selected[data-x]");
    if (selected) {
      viewport.scrollLeft = Math.max(0, Number.parseFloat(selected.dataset.x) - viewport.clientWidth / 2);
    } else if (frise.hasAttribute("data-filtered")) {
      const positions = Array.from(viewport.querySelectorAll("[data-frise-marker]"), (marker) => Number.parseFloat(marker.dataset.x));
      if (positions.length) {
        viewport.scrollLeft = Math.max(0, Math.min(...positions) - 48);
      }
    }
    frise.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) {
        return;
      }
      const jump = target.closest("[data-frise-jump]");
      if (jump) {
        const band = document.getElementById(jump.getAttribute("data-frise-jump"));
        if (band) {
          event.preventDefault();
          scrollFriseTo(viewport, Number.parseFloat(band.dataset.x || "0"), false);
        }
        return;
      }
      const arrow = target.closest("[data-frise-scroll]");
      if (arrow) {
        const direction = Number.parseInt(arrow.getAttribute("data-frise-scroll"), 10);
        viewport.scrollBy({ left: direction * viewport.clientWidth * 0.75, behavior: prefersReducedMotion.matches ? "auto" : "smooth" });
        return;
      }
      const marker = target.closest("[data-frise-marker]");
      if (marker) {
        frise.querySelectorAll("[data-frise-marker].is-selected").forEach((item) => item.classList.remove("is-selected"));
        marker.classList.add("is-selected");
      }
    });
  });
}

const filtersDesktop = window.matchMedia("(min-width: 64rem)");

function syncFilterPanels() {
  document.querySelectorAll("details[data-filters-panel]").forEach((panel) => {
    panel.open = filtersDesktop.matches || panel.hasAttribute("data-active");
  });
}

document.addEventListener("click", (event) => {
  const summary = event.target instanceof Element ? event.target.closest("summary") : null;
  if (summary && filtersDesktop.matches && summary.parentElement?.hasAttribute("data-filters-panel")) {
    event.preventDefault();
  }
});
filtersDesktop.addEventListener("change", syncFilterPanels);

function measureHeader() {
  const header = document.querySelector(".site-header");
  if (header) {
    document.documentElement.style.setProperty("--header-offset", `${header.offsetHeight}px`);
  }
}

applyGeometry();
initFrise();
syncFilterPanels();
measureHeader();
window.addEventListener("resize", measureHeader);

const currentNavLink = document.querySelector(".nav a[aria-current]");
if (currentNavLink && currentNavLink.parentElement) {
  const nav = currentNavLink.parentElement;
  const overflow = currentNavLink.offsetLeft + currentNavLink.offsetWidth - nav.clientWidth;
  if (overflow > 0) {
    nav.scrollLeft = overflow + 24;
  }
}

document.addEventListener("htmx:afterSwap", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) {
    return;
  }
  applyGeometry(target);
  if (target.id === "chrono-panel") {
    target.classList.remove("chrono-panel--idle");
    const heading = target.querySelector("h2[tabindex]");
    if (heading) {
      heading.focus({ preventScroll: true });
    }
    if (target.getBoundingClientRect().top < 0) {
      target.scrollIntoView({ block: "start", behavior: prefersReducedMotion.matches ? "auto" : "smooth" });
    }
  }
});

document.addEventListener("htmx:historyRestore", () => {
  document.querySelectorAll("[data-frise]").forEach((frise) => frise.removeAttribute("data-ready"));
  applyGeometry();
  initFrise();
  syncFilterPanels();
});
