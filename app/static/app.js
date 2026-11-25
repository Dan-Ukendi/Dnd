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

function ownTabs(container) {
  const list = container.querySelector(":scope > .tabs__list");
  return list ? Array.from(list.querySelectorAll(':scope > [role="tab"]')) : [];
}

function selectTab(container, tab, focus) {
  ownTabs(container).forEach((item) => {
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

function revealTab(tab) {
  let current = tab;
  while (current) {
    const container = current.closest("[data-tabs]");
    if (!container) {
      return;
    }
    selectTab(container, current, false);
    const panel = container.closest('[role="tabpanel"]');
    current = panel ? document.querySelector(`[role="tab"][aria-controls="${CSS.escape(panel.id)}"]`) : null;
  }
}

function tabFromHash() {
  const key = window.location.hash.slice(1);
  return key ? document.querySelector(`[data-tabs] [role="tab"][data-tab="${CSS.escape(key)}"]`) : null;
}

document.querySelectorAll("[data-tabs]").forEach((container) => {
  const tabs = ownTabs(container);
  if (!tabs.length) {
    return;
  }
  container.classList.add("tabs--ready");
  selectTab(container, tabs[0], false);
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      selectTab(container, tab, false);
      history.replaceState(null, "", `#${tab.dataset.tab}`);
    });
  });
  container.querySelector(":scope > .tabs__list").addEventListener("keydown", (event) => {
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

const initialTab = tabFromHash();
if (initialTab) {
  revealTab(initialTab);
}

window.addEventListener("hashchange", () => {
  const tab = tabFromHash();
  if (tab) {
    revealTab(tab);
  }
});

function visibleTreeItems(tree) {
  return Array.from(tree.querySelectorAll('[role="treeitem"]')).filter((item) => {
    let parent = item.parentElement ? item.parentElement.closest('[role="treeitem"]') : null;
    while (parent && tree.contains(parent)) {
      if (parent.getAttribute("aria-expanded") === "false") {
        return false;
      }
      parent = parent.parentElement ? parent.parentElement.closest('[role="treeitem"]') : null;
    }
    return true;
  });
}

function setTreeItemExpanded(item, expanded) {
  if (!item.hasAttribute("aria-expanded")) {
    return;
  }
  item.setAttribute("aria-expanded", String(expanded));
  const panel = item.querySelector(":scope > .tree__panel");
  if (panel) {
    panel.inert = !expanded;
  }
}

function focusTreeItem(tree, item) {
  tree.querySelectorAll('[role="treeitem"]').forEach((other) => {
    other.tabIndex = other === item ? 0 : -1;
  });
  item.focus();
}

function initTree(tree) {
  if (tree.dataset.ready) {
    return;
  }
  tree.dataset.ready = "true";
  const items = Array.from(tree.querySelectorAll('[role="treeitem"]'));
  if (!items.length) {
    return;
  }
  items.forEach((item, index) => {
    item.tabIndex = index === 0 ? 0 : -1;
  });
  tree.querySelectorAll("a").forEach((link) => {
    link.tabIndex = -1;
  });
  tree.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const item = target ? target.closest('[role="treeitem"]') : null;
    if (!item || !tree.contains(item) || target.closest("a")) {
      return;
    }
    if (!target.closest(".tree__row")) {
      return;
    }
    focusTreeItem(tree, item);
    if (item.hasAttribute("aria-expanded")) {
      setTreeItemExpanded(item, item.getAttribute("aria-expanded") !== "true");
    }
  });
  tree.addEventListener("keydown", (event) => {
    const item = document.activeElement instanceof Element ? document.activeElement.closest('[role="treeitem"]') : null;
    if (!item || !tree.contains(item) || event.altKey || event.ctrlKey || event.metaKey) {
      return;
    }
    const visible = visibleTreeItems(tree);
    const index = visible.indexOf(item);
    const expanded = item.getAttribute("aria-expanded");
    const parent = item.parentElement ? item.parentElement.closest('[role="treeitem"]') : null;
    let next = null;
    switch (event.key) {
      case "ArrowDown":
        next = visible[index + 1];
        break;
      case "ArrowUp":
        next = visible[index - 1];
        break;
      case "Home":
        next = visible[0];
        break;
      case "End":
        next = visible[visible.length - 1];
        break;
      case "ArrowRight":
        if (expanded === "false") {
          setTreeItemExpanded(item, true);
        } else if (expanded === "true") {
          next = item.querySelector('[role="group"] > [role="treeitem"]');
        }
        break;
      case "ArrowLeft":
        if (expanded === "true") {
          setTreeItemExpanded(item, false);
        } else if (parent && tree.contains(parent)) {
          next = parent;
        }
        break;
      case "Enter":
        if (item.dataset.href) {
          window.location.assign(item.dataset.href);
        } else if (expanded !== null) {
          setTreeItemExpanded(item, expanded !== "true");
        }
        break;
      case " ":
        if (expanded !== null) {
          setTreeItemExpanded(item, expanded !== "true");
        }
        break;
      case "*": {
        const group = item.parentElement;
        if (group) {
          Array.from(group.children).forEach((sibling) => setTreeItemExpanded(sibling, true));
        }
        break;
      }
      default:
        return;
    }
    event.preventDefault();
    if (next) {
      focusTreeItem(tree, next);
    }
  });
}

function initStructures(root = document) {
  root.querySelectorAll("[data-tree]").forEach(initTree);
}

document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) {
    return;
  }
  const treeButton = target.closest("[data-tree-all]");
  if (treeButton) {
    const expanded = treeButton.getAttribute("data-tree-all") === "expand";
    (treeButton.getAttribute("aria-controls") || "").split(/\s+/).forEach((id) => {
      const tree = id ? document.getElementById(id) : null;
      if (tree) {
        tree.querySelectorAll('[role="treeitem"][aria-expanded]').forEach((item) => setTreeItemExpanded(item, expanded));
        const focused = tree.querySelector('[role="treeitem"][tabindex="0"]');
        if (focused && !visibleTreeItems(tree).includes(focused)) {
          tree.querySelectorAll('[role="treeitem"]').forEach((item, index) => {
            item.tabIndex = index === 0 ? 0 : -1;
          });
        }
      }
    });
    return;
  }
  const disclosureButton = target.closest("[data-disclosure-all]");
  if (disclosureButton) {
    const open = disclosureButton.getAttribute("data-disclosure-all") === "open";
    const container = document.getElementById(disclosureButton.getAttribute("aria-controls") || "");
    if (container) {
      container.querySelectorAll("details").forEach((details) => {
        details.open = open;
      });
    }
  }
});

initStructures();

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
  initStructures(target);
  if (target.hasAttribute("data-territory")) {
    const heading = target.querySelector("[data-territory-focus]");
    if (heading) {
      heading.focus({ preventScroll: true });
    }
    if (target.getBoundingClientRect().top < 0) {
      target.scrollIntoView({ block: "start", behavior: prefersReducedMotion.matches ? "auto" : "smooth" });
    }
  }
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
  document.querySelectorAll("[data-tree]").forEach((tree) => tree.removeAttribute("data-ready"));
  initStructures();
  applyGeometry();
  initFrise();
  syncFilterPanels();
});
