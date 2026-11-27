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

function initTabs(root = document) {
  root.querySelectorAll("[data-tabs]").forEach(initTabContainer);
}

function initTabContainer(container) {
  const tabs = ownTabs(container);
  if (!tabs.length || container.dataset.tabsReady) {
    return;
  }
  container.dataset.tabsReady = "true";
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
}

initTabs();

function revealHashTarget() {
  const tab = tabFromHash();
  if (tab) {
    revealTab(tab);
    return;
  }
  const key = window.location.hash.slice(1);
  const target = key ? document.getElementById(key) : null;
  const panel = target ? target.closest('[role="tabpanel"]') : null;
  const owner = panel ? document.querySelector(`[data-tabs] [role="tab"][aria-controls="${CSS.escape(panel.id)}"]`) : null;
  if (!owner) {
    return;
  }
  revealTab(owner);
  if (target instanceof HTMLDetailsElement) {
    target.open = true;
  }
  target.scrollIntoView({ block: "start" });
}

revealHashTarget();
window.addEventListener("hashchange", revealHashTarget);

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

function revealCurrentNavLink() {
  const currentNavLink = document.querySelector(".nav a[aria-current]");
  if (currentNavLink && currentNavLink.parentElement) {
    const nav = currentNavLink.parentElement;
    const overflow = currentNavLink.offsetLeft + currentNavLink.offsetWidth - nav.clientWidth;
    if (overflow > 0) {
      nav.scrollLeft = overflow + 24;
    }
  }
}

revealCurrentNavLink();

function initPage() {
  syncPanels();
  markScrollableTables();
  initTabs();
  revealHashTarget();
  initStructures();
  applyGeometry();
  initFrise();
  syncFilterPanels();
  measureHeader();
  revealCurrentNavLink();
}

document.addEventListener("htmx:afterSwap", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) {
    return;
  }
  if (event.detail && event.detail.boosted) {
    hidePreview();
    previewIndexes.clear();
    initPage();
    const main = document.getElementById("contenu");
    if (main) {
      main.tabIndex = -1;
      main.focus({ preventScroll: true });
    }
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
  hidePreview();
  document.querySelectorAll("[data-frise]").forEach((frise) => frise.removeAttribute("data-ready"));
  document.querySelectorAll("[data-tree]").forEach((tree) => tree.removeAttribute("data-ready"));
  document.querySelectorAll("[data-tabs]").forEach((container) => container.removeAttribute("data-tabs-ready"));
  initPage();
});

function leaveBoostedRequest(event) {
  const link = event.detail ? event.detail.elt : null;
  if (event.detail && event.detail.boosted && link instanceof HTMLAnchorElement) {
    window.location.assign(link.href);
  }
}

document.addEventListener("htmx:beforeHistorySave", () => {
  hidePreview();
  const styled = Array.from(document.body.querySelectorAll("[style]"), (node) => [node, node.style.cssText]);
  styled.forEach(([node]) => node.removeAttribute("style"));
  window.setTimeout(() => styled.forEach(([node, css]) => {
    node.style.cssText = css;
  }), 0);
});
document.addEventListener("htmx:responseError", leaveBoostedRequest);
document.addEventListener("htmx:sendError", leaveBoostedRequest);

const FICHE_PATH = /^\/(mj|joueur)\/articles\/([0-9a-f-]{36})$/i;
const PREVIEW_KINDS = new Set(["personnage", "lieu", "objet", "organisation", "lore", "aventure"]);
const previewIndexes = new Map();
let previewTip = null;
let previewLink = null;
let previewTimer = 0;

function loadPreviews(scope) {
  if (!previewIndexes.has(scope)) {
    const request = fetch(`/${scope}/liens/apercus`, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => (data && data.fiches) || {})
      .catch(() => {
        previewIndexes.delete(scope);
        return {};
      });
    previewIndexes.set(scope, request);
  }
  return previewIndexes.get(scope);
}

function previewElement() {
  if (!previewTip || !previewTip.isConnected) {
    previewTip = document.getElementById("apercu-fiche");
  }
  if (!previewTip) {
    previewTip = document.createElement("div");
    previewTip.id = "apercu-fiche";
    previewTip.setAttribute("role", "tooltip");
    previewTip.hidden = true;
    ["type", "title", "text"].forEach((part) => {
      const span = document.createElement("span");
      span.className = `link-preview__${part}`;
      previewTip.append(span);
    });
    document.documentElement.append(previewTip);
  }
  return previewTip;
}

function hidePreview() {
  window.clearTimeout(previewTimer);
  if (previewLink) {
    previewLink.removeAttribute("aria-describedby");
  }
  previewLink = null;
  if (previewTip) {
    previewTip.hidden = true;
  }
}

function placePreview(tip, link) {
  const box = link.getClientRects()[0] || link.getBoundingClientRect();
  tip.hidden = false;
  const left = Math.max(8, Math.min(box.left, window.innerWidth - tip.offsetWidth - 8));
  let top = box.bottom + 8;
  if (top + tip.offsetHeight > window.innerHeight - 8) {
    top = Math.max(8, box.top - tip.offsetHeight - 8);
  }
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function showPreview(link) {
  const match = FICHE_PATH.exec(link.getAttribute("href") || "");
  if (!match) {
    return;
  }
  previewLink = link;
  loadPreviews(match[1].toLowerCase()).then((fiches) => {
    const key = match[2].toLowerCase();
    const fiche = Object.prototype.hasOwnProperty.call(fiches, key) ? fiches[key] : null;
    if (previewLink !== link || !fiche || !link.isConnected) {
      return;
    }
    const tip = previewElement();
    tip.className = PREVIEW_KINDS.has(fiche.kind) ? `link-preview link-preview--${fiche.kind}` : "link-preview";
    const [type, title, text] = tip.children;
    type.textContent = fiche.type || "";
    title.textContent = fiche.title || "";
    text.textContent = fiche.excerpt || "";
    placePreview(tip, link);
    link.setAttribute("aria-describedby", tip.id);
  });
}

function autolinkOf(node) {
  return node instanceof Element ? node.closest("a.autolink") : null;
}

document.addEventListener("mouseover", (event) => {
  const link = autolinkOf(event.target);
  if (!link || link === previewLink) {
    return;
  }
  window.clearTimeout(previewTimer);
  previewTimer = window.setTimeout(() => showPreview(link), 180);
});

document.addEventListener("mouseout", (event) => {
  const link = autolinkOf(event.target);
  if (!link || link.contains(event.relatedTarget)) {
    return;
  }
  window.clearTimeout(previewTimer);
  if (link === previewLink && document.activeElement !== link) {
    hidePreview();
  }
});

document.addEventListener("focusin", (event) => {
  const link = autolinkOf(event.target);
  if (link) {
    window.clearTimeout(previewTimer);
    showPreview(link);
  }
});

document.addEventListener("focusout", (event) => {
  if (previewLink && autolinkOf(event.target) === previewLink) {
    hidePreview();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && previewLink) {
    hidePreview();
  }
});

window.addEventListener("scroll", hidePreview, { passive: true, capture: true });

const DICE_ICON =
  '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
  '<path d="M12 2.8 20 7.4v9.2L12 21.2 4 16.6V7.4z"/><path d="M12 2.8 7.6 14.6h8.8z"/>' +
  '<path d="M4 7.4l3.6 7.2M20 7.4l-3.6 7.2M7.6 14.6 12 21.2l4.4-6.6"/></svg>';
const DICE_MODES = [
  ["normal", "Normal"],
  ["advantage", "Avantage"],
  ["disadvantage", "Désavantage"],
];
const DICE_LOG_SIZE = 8;
const D20_COLUMNS = [
  [/sauvegarde/i, "save"],
  [/toucher|attaque/i, "attack"],
  [/modificateur|bonus/i, "check"],
];
const MODIFIER_CELL = /^\s*([+\-−–]\s?\d{1,2})\s*(\(.*\))?\s*$/;
const DICE_TEXT = /(^|[^\w])(\d{1,3}\s*d\s*\d{1,4}(?:\s*[+\-−–]\s*\d{1,4}(?:\s*d\s*\d{1,4})?(?![\w]))*)/gi;

function csrfToken() {
  try {
    return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRF-Token"] || "";
  } catch (error) {
    return "";
  }
}

function storedDiceMode() {
  try {
    return window.sessionStorage.getItem("dice-mode") || "normal";
  } catch (error) {
    return "normal";
  }
}

function diceTray() {
  let tray = document.getElementById("dice-tray");
  if (tray) {
    return tray;
  }
  tray = document.createElement("aside");
  tray.id = "dice-tray";
  tray.className = "dice-tray";
  tray.setAttribute("aria-label", "Jets de dés");
  const current = storedDiceMode();
  const modes = DICE_MODES.map(
    ([value, label]) =>
      `<label class="dice-tray__mode"><input type="radio" name="dice-mode" value="${value}"${
        value === current ? " checked" : ""
      }><span>${label}</span></label>`,
  ).join("");
  tray.innerHTML =
    `<button type="button" class="dice-tray__toggle" aria-expanded="false" aria-controls="dice-tray-panel">${DICE_ICON}<span>Dés</span></button>` +
    '<div class="dice-tray__panel" id="dice-tray-panel" hidden>' +
    '<div class="dice-tray__head"><p class="dice-tray__title">Jets de dés</p>' +
    '<button type="button" class="dice-tray__close" aria-label="Fermer les jets de dés">×</button></div>' +
    `<fieldset class="dice-tray__modes"><legend class="visually-hidden">Jets de d20</legend>${modes}</fieldset>` +
    '<form class="dice-tray__free"><label class="visually-hidden" for="dice-tray-expression">Formule de dés</label>' +
    '<input id="dice-tray-expression" name="expression" type="text" maxlength="60" placeholder="2d6+3" autocomplete="off" required>' +
    '<button type="submit" class="btn btn--small">Lancer</button></form>' +
    '<ol class="dice-tray__log" aria-live="polite"></ol></div>';
  document.body.appendChild(tray);
  return tray;
}

function setDiceTrayOpen(open) {
  const tray = diceTray();
  const panel = tray.querySelector(".dice-tray__panel");
  const toggle = tray.querySelector(".dice-tray__toggle");
  panel.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
  tray.classList.toggle("dice-tray--open", open);
}

function diceMode() {
  const checked = diceTray().querySelector('input[name="dice-mode"]:checked');
  return checked ? checked.value : "normal";
}

function pushDiceLog(item) {
  const log = diceTray().querySelector(".dice-tray__log");
  log.prepend(item);
  while (log.children.length > DICE_LOG_SIZE) {
    log.lastElementChild.remove();
  }
  setDiceTrayOpen(true);
}

function pushDiceMessage(detail) {
  const item = document.createElement("li");
  item.className = `dice-result dice-result--message${detail.kind === "error" ? " dice-result--error" : ""}`;
  const text = document.createElement("p");
  text.className = "dice-result__note";
  text.textContent = detail.text || "";
  item.append(text);
  (detail.details || []).forEach((line) => {
    const row = document.createElement("p");
    row.className = "dice-result__detail";
    row.textContent = line;
    item.append(row);
  });
  pushDiceLog(item);
}

async function sendRoll(values) {
  const body = new URLSearchParams(values);
  body.set("mode", diceMode());
  try {
    const response = await fetch("/des", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken(), "Content-Type": "application/x-www-form-urlencoded" },
      body,
      credentials: "same-origin",
    });
    const text = await response.text();
    const type = response.headers.get("content-type") || "";
    const template = document.createElement("template");
    template.innerHTML = type.startsWith("text/html") ? text.trim() : "";
    const item = template.content.firstElementChild;
    if (item && item.matches("li.dice-result")) {
      pushDiceLog(item);
      return;
    }
    pushDiceMessage({ text: "Le jet n'a pas pu être lancé.", kind: "error" });
  } catch (error) {
    pushDiceMessage({ text: "Le jet n'a pas pu être lancé (connexion).", kind: "error" });
  }
}

function rollButton(text, expression, kind, label, damage) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "dice-roll";
  button.dataset.roll = expression;
  button.dataset.rollKind = kind;
  button.dataset.rollLabel = label;
  if (damage && damage.length) {
    button.dataset.rollDamage = damage.join(";");
  }
  button.title = `Lancer ${expression}`;
  button.textContent = text;
  return button;
}

function diceInText(text) {
  return Array.from(text.matchAll(DICE_TEXT), (match) => match[2]);
}

function wrapDiceText(cell, label) {
  const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.parentElement.closest("a, button")) {
      nodes.push(node);
    }
  }
  nodes.forEach((node) => {
    const text = node.nodeValue;
    const parts = [];
    let position = 0;
    for (const match of text.matchAll(DICE_TEXT)) {
      const start = match.index + match[1].length;
      parts.push(document.createTextNode(text.slice(position, start)));
      parts.push(rollButton(match[2], match[2], "damage", label));
      position = start + match[2].length;
    }
    if (parts.length) {
      parts.push(document.createTextNode(text.slice(position)));
      node.replaceWith(...parts);
    }
  });
}

function d20Kind(header) {
  const found = D20_COLUMNS.find(([pattern]) => pattern.test(header));
  return found ? found[1] : null;
}

function enhanceDiceTables(root = document) {
  root.querySelectorAll("[data-dice-scope] table").forEach((table) => {
    if (table.dataset.diceReady) {
      return;
    }
    table.dataset.diceReady = "1";
    const headers = Array.from(table.querySelectorAll("thead th"), (cell) => cell.textContent.trim());
    table.querySelectorAll("tbody tr").forEach((row) => {
      const cells = Array.from(row.children);
      const name = cells.length ? cells[0].textContent.trim() : "";
      const damage = cells.flatMap((cell) => diceInText(cell.textContent));
      cells.forEach((cell, index) => {
        if (index === 0) {
          return;
        }
        const kind = d20Kind(headers[index] || "");
        const match = kind ? MODIFIER_CELL.exec(cell.textContent) : null;
        if (match) {
          const modifier = match[1].replace(/[−–]/, "-").replace(/\s/g, "");
          const label = kind === "save" ? `${name}, sauvegarde` : name;
          const button = rollButton(cell.textContent.trim(), modifier, kind, label, kind === "attack" ? damage : []);
          cell.replaceChildren(button);
        } else {
          wrapDiceText(cell, name);
        }
      });
    });
  });
}

function initDice(root = document) {
  enhanceDiceTables(root);
  if (document.querySelector("[data-roll], [data-dice-scope]")) {
    diceTray();
  }
}

document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) {
    return;
  }
  const button = target.closest("button[data-roll]");
  if (button) {
    event.preventDefault();
    sendRoll({
      expression: button.dataset.roll,
      kind: button.dataset.rollKind || "damage",
      label: button.dataset.rollLabel || "",
      damage: button.dataset.rollDamage || "",
      crit: button.dataset.rollCrit || "",
    });
    return;
  }
  if (target.closest(".dice-tray__toggle")) {
    setDiceTrayOpen(diceTray().querySelector(".dice-tray__panel").hidden);
  } else if (target.closest(".dice-tray__close")) {
    setDiceTrayOpen(false);
  }
});

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.name === "dice-mode") {
    try {
      window.sessionStorage.setItem("dice-mode", target.value);
    } catch (error) {
      // Storage can be unavailable: the choice still applies to this page.
    }
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form instanceof HTMLFormElement && form.classList.contains("dice-tray__free")) {
    event.preventDefault();
    const input = form.elements.namedItem("expression");
    sendRoll({ expression: input.value, kind: "free", label: "" });
  }
});

document.addEventListener("play-message", (event) => {
  pushDiceMessage(event.detail || {});
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && document.querySelector(".dice-tray--open")) {
    setDiceTrayOpen(false);
  }
});

initDice();
document.addEventListener("htmx:afterSettle", (event) => {
  if (event.detail && event.detail.boosted) {
    initDice();
  }
});
document.addEventListener("htmx:historyRestore", () => initDice());

const TOKEN_COLOR = /^#[0-9a-fA-F]{6}$/;

function placeBattleTokens(root = document) {
  root.querySelectorAll("[data-battle-grid]").forEach((grid) => {
    const cols = Number.parseInt(grid.dataset.cols, 10);
    const rows = Number.parseInt(grid.dataset.rows, 10);
    if (!(cols > 0 && rows > 0)) {
      return;
    }
    grid.style.setProperty("--cell-width", `${grid.clientWidth / cols}px`);
    grid.querySelectorAll("[data-battle-token]").forEach((token) => {
      const col = Number.parseInt(token.dataset.col, 10);
      const row = Number.parseInt(token.dataset.row, 10);
      if (Number.isFinite(col) && Number.isFinite(row)) {
        token.style.left = `${((col + 0.5) / cols) * 100}%`;
        token.style.top = `${((row + 0.5) / rows) * 100}%`;
      }
    });
  });
  root.querySelectorAll("[data-battle-token][data-color]").forEach((token) => {
    if (TOKEN_COLOR.test(token.dataset.color)) {
      token.style.setProperty("--token-color", token.dataset.color);
    }
  });
  root.querySelectorAll("[data-battle-token][data-facing]").forEach((token) => {
    const facing = Number.parseInt(token.dataset.facing, 10);
    if (facing >= 0 && facing < 8) {
      token.style.setProperty("--facing", `${facing * 45}deg`);
    }
  });
}

async function saveTokenFacing(board, token, facing) {
  const body = new URLSearchParams({ facing: String(facing) });
  try {
    const response = await fetch(`${board.dataset.moveUrl}${token.dataset.battleToken}/orientation`, {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken(), "Content-Type": "application/x-www-form-urlencoded" },
      body,
      credentials: "same-origin",
    });
    return response.ok;
  } catch (error) {
    return false;
  }
}

function battleCell(grid, x, y) {
  const rect = grid.getBoundingClientRect();
  if (x < rect.left || x >= rect.right || y < rect.top || y >= rect.bottom) {
    return null;
  }
  const cols = Number.parseInt(grid.dataset.cols, 10);
  const rows = Number.parseInt(grid.dataset.rows, 10);
  return {
    col: Math.min(cols - 1, Math.floor(((x - rect.left) / rect.width) * cols)),
    row: Math.min(rows - 1, Math.floor(((y - rect.top) / rect.height) * rows)),
  };
}

function resetDraggedToken(token) {
  token.classList.remove("is-dragging");
  ["left", "top", "width", "height"].forEach((property) => token.style.removeProperty(property));
}

async function saveTokenCell(board, token, cell) {
  const body = new URLSearchParams({ col: String(cell.col), row: String(cell.row) });
  try {
    const response = await fetch(`${board.dataset.moveUrl}${token.dataset.battleToken}`, {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken(), "Content-Type": "application/x-www-form-urlencoded" },
      body,
      credentials: "same-origin",
    });
    return response.ok;
  } catch (error) {
    return false;
  }
}

document.addEventListener("pointerdown", (event) => {
  const token = event.target instanceof Element ? event.target.closest("[data-battle-dm] [data-battle-token]") : null;
  const board = token ? token.closest("[data-battle-dm]") : null;
  const grid = board ? board.querySelector("[data-battle-grid]") : null;
  if (!token || !grid || event.button !== 0) {
    return;
  }
  event.preventDefault();
  const start = { x: event.clientX, y: event.clientY };
  const size = token.getBoundingClientRect();
  token.style.width = `${size.width}px`;
  token.style.height = `${size.height}px`;
  token.classList.add("is-dragging");
  const follow = (moveEvent) => {
    token.style.left = `${moveEvent.clientX}px`;
    token.style.top = `${moveEvent.clientY}px`;
  };
  follow(event);
  token.setPointerCapture(event.pointerId);
  token.addEventListener("pointermove", follow);
  token.addEventListener(
    "pointercancel",
    () => {
      token.removeEventListener("pointermove", follow);
      resetDraggedToken(token);
      placeBattleTokens(board);
    },
    { once: true },
  );
  token.addEventListener(
    "pointerup",
    async (upEvent) => {
      token.removeEventListener("pointermove", follow);
      resetDraggedToken(token);
      const moved = Math.hypot(upEvent.clientX - start.x, upEvent.clientY - start.y) > 4;
      if (!moved && token.parentElement === grid) {
        const current = Number.parseInt(token.dataset.facing ?? "0", 10) || 0;
        const facing = (current + (upEvent.shiftKey ? 7 : 1)) % 8;
        if (await saveTokenFacing(board, token, facing)) {
          token.dataset.facing = String(facing);
        }
        placeBattleTokens(board);
        return;
      }
      const cell = battleCell(grid, upEvent.clientX, upEvent.clientY);
      if (cell && (await saveTokenCell(board, token, cell))) {
        if (token.parentElement !== grid) {
          token.parentElement.removeChild(token);
          grid.append(token);
        }
        token.dataset.col = String(cell.col);
        token.dataset.row = String(cell.row);
      }
      placeBattleTokens(board);
    },
    { once: true },
  );
});

placeBattleTokens();
document.addEventListener("htmx:afterSettle", () => placeBattleTokens());
window.addEventListener("resize", () => placeBattleTokens());
document.addEventListener("load", (event) => {
  if (event.target instanceof HTMLImageElement && event.target.closest(".battle-map")) {
    placeBattleTokens();
  }
}, true);
