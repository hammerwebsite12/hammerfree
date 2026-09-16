/* QuickPlay — gamepad / controller navigation (Gamepad API) */
(function () {
  "use strict";

  const TAB_ORDER = ["browse", "library", "downloads", "settings", "help"];
  const REPEAT_MS = 170;
  const STICK_DEAD = 0.42;
  const STICK_REPEAT_MS = 220;

  const hooks = {};
  let enabled = false;
  let focused = null;
  let prevButtons = {};
  let lastNavAt = 0;
  let stickDirHeld = null;
  let stickHeldAt = 0;
  let rafId = 0;
  let httpPollId = 0;
  let detectPollId = 0;

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function isVisible(el) {
    if (!el || el.disabled || el.hidden) return false;
    if (el.classList.contains("hidden")) return false;
    const modal = el.closest(".modal, .game-detail-modal");
    if (modal && modal.classList.contains("hidden")) return false;
    const panel = el.closest(".panel");
    if (panel && !panel.classList.contains("active") && !modal) return false;
    return el.getClientRects().length > 0;
  }

  function getModal() {
    if (window.BigPicture?.isOpen?.()) return null;
    const detail = $("#gameDetailModal");
    if (detail && !detail.classList.contains("hidden")) return detail;
    const modals = [...document.querySelectorAll(".modal:not(.hidden)")];
    return modals[0] || null;
  }

  function collectFocusables() {
    const modal = getModal();
    if (modal) {
      const exeItems = [...modal.querySelectorAll("#exeList li")].filter(isVisible);
      const buttons = [...modal.querySelectorAll("button:not([disabled])")].filter(isVisible);
      return [...exeItems, ...buttons];
    }

    const tab = document.querySelector(".tab.active")?.dataset.tab || "browse";
    const items = [];

    if (tab === "browse") {
      ["#searchBtn", "#prevPage", "#nextPage"].forEach((sel) => {
        const el = $(sel);
        if (el) items.push(el);
      });
      document.querySelectorAll(".sort-btn").forEach((el) => items.push(el));
      document.querySelectorAll("#categoryBar .category-chip").forEach((el) => items.push(el));
      document.querySelectorAll("#gameGrid .game-item").forEach((el) => items.push(el));
    } else if (tab === "library") {
      document.querySelectorAll("#libraryGrid .library-card").forEach((el) => items.push(el));
    } else if (tab === "downloads") {
      const clear = $("#clearLogs");
      if (clear) items.push(clear);
      document.querySelectorAll("#taskList .task-controls button").forEach((el) => items.push(el));
      document.querySelectorAll("#pendingExeList button").forEach((el) => items.push(el));
    } else if (tab === "settings") {
      document.querySelectorAll(
        "#panel-settings select, #panel-settings button, #panel-settings input[type=checkbox], #panel-settings input[type=range]"
      ).forEach((el) => items.push(el));
    } else if (tab === "help") {
      document.querySelectorAll("#panel-help button").forEach((el) => items.push(el));
      const follow = $("#followPageBtn");
      if (follow) items.push(follow);
    }

    return items.filter(isVisible);
  }

  function clearFocus() {
    if (focused) {
      focused.classList.remove("gp-focused");
      focused = null;
    }
  }

  function focusElement(el) {
    if (!el || !enabled) return;
    clearFocus();
    focused = el;
    // EXE picker uses .selected only — no controller focus ring on list rows.
    if (el.tagName === "LI" && el.closest("#exeList")) {
      el.click();
      return;
    }
    el.classList.add("gp-focused");
    try {
      el.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
    } catch (_) {}
  }

  function blur() {
    clearFocus();
  }

  function focusFirst() {
    const items = collectFocusables();
    if (items.length) focusElement(items[0]);
  }

  function gridItems(grid) {
    return [...grid.querySelectorAll(".game-item, .library-card")].filter(isVisible);
  }

  function gridColumns(items) {
    if (items.length < 2) return 1;
    const top = items[0].offsetTop;
    let cols = 1;
    for (let i = 1; i < items.length; i++) {
      if (items[i].offsetTop === top) cols += 1;
      else break;
    }
    return cols || 1;
  }

  function gridHost(el) {
    return el?.closest(".game-grid");
  }

  function navigateGrid(dir) {
    const host = gridHost(focused);
    if (!host) return false;
    const items = gridItems(host);
    const current = focused.classList.contains("library-card") || focused.classList.contains("game-item")
      ? focused
      : focused.closest(".game-item, .library-card");
    let idx = items.indexOf(current);
    if (idx < 0) return false;

    const cols = gridColumns(items);
    let next = idx;
    if (dir === "left") next = idx - 1;
    if (dir === "right") next = idx + 1;
    if (dir === "up") next = idx - cols;
    if (dir === "down") next = idx + cols;

    if (next >= 0 && next < items.length) {
      focusElement(items[next]);
      return true;
    }

    if (dir === "up" && idx < cols) {
      const all = collectFocusables();
      const firstGrid = all.findIndex((el) => el.classList.contains("game-item") || el.classList.contains("library-card"));
      if (firstGrid > 0) focusElement(all[firstGrid - 1]);
      return true;
    }
    return false;
  }

  function navigate(dir) {
    if (!enabled) return;
    const now = Date.now();
    if (now - lastNavAt < REPEAT_MS) return;
    lastNavAt = now;

    if (!focused) {
      focusFirst();
      return;
    }

    if (gridHost(focused) && navigateGrid(dir)) return;

    const items = collectFocusables();
    const idx = items.indexOf(focused);
    if (idx < 0) {
      focusFirst();
      return;
    }

    let next = idx;
    if (dir === "left" || dir === "up") next = Math.max(0, idx - 1);
    if (dir === "right" || dir === "down") next = Math.min(items.length - 1, idx + 1);

    if (dir === "down" && focused.type === "range") {
      const step = parseFloat(focused.step) || 1;
      const max = parseFloat(focused.max);
      const min = parseFloat(focused.min);
      focused.value = Math.min(max, parseFloat(focused.value) + step);
      focused.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
    if (dir === "up" && focused.type === "range") {
      const step = parseFloat(focused.step) || 1;
      const max = parseFloat(focused.max);
      const min = parseFloat(focused.min);
      focused.value = Math.max(min, parseFloat(focused.value) - step);
      focused.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }

    if (next !== idx) focusElement(items[next]);
  }

  function activate() {
    if (!focused) {
      focusFirst();
      return;
    }

    const libCard = focused.classList.contains("library-card")
      ? focused
      : focused.closest(".library-card");
    if (libCard && !getModal()) {
      libCard.querySelector(".play-btn")?.click();
      return;
    }

    if (focused.classList.contains("game-item")) {
      focused.click();
      return;
    }

    if (focused.tagName === "LI" && focused.closest("#exeList")) {
      focused.click();
      return;
    }

    if (focused.type === "checkbox") {
      focused.checked = !focused.checked;
      focused.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }

    if (focused.tagName === "SELECT") {
      focused.focus();
      focused.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
      return;
    }

    focused.click();
  }

  function back() {
    const modal = getModal();
    if (modal) {
      if (modal.id === "gameDetailModal") {
        hooks.closeGameDetail?.();
      } else if (modal.id === "exeModal") {
        hooks.hideExeModal?.();
      } else {
        const cancel = modal.querySelector(
          "#confirmCancel, #licenseClose, #announcementOk, #exeLater, #detailClose"
        );
        if (cancel) cancel.click();
        else modal.querySelector(".modal-backdrop")?.click();
      }
      setTimeout(refresh, 30);
      return;
    }
    clearFocus();
  }

  function switchTabRel(delta) {
    const tab = document.querySelector(".tab.active")?.dataset.tab || "browse";
    const idx = TAB_ORDER.indexOf(tab);
    if (idx < 0) return;
    const next = TAB_ORDER[(idx + delta + TAB_ORDER.length) % TAB_ORDER.length];
    hooks.switchTab?.(next);
    setTimeout(refresh, 60);
  }

  function dpadDir(gp) {
    if (gp.buttons[12]?.pressed) return "up";
    if (gp.buttons[13]?.pressed) return "down";
    if (gp.buttons[14]?.pressed) return "left";
    if (gp.buttons[15]?.pressed) return "right";
    // Some controllers expose the D-pad as axes instead of buttons.
    const ax6 = gp.axes[6] ?? gp.axes[9];
    const ax7 = gp.axes[7] ?? gp.axes[10];
    if (ax6 != null && ax7 != null) {
      if (ax6 < -0.5) return "left";
      if (ax6 > 0.5) return "right";
      if (ax7 < -0.5) return "up";
      if (ax7 > 0.5) return "down";
    }
    const x = gp.axes[0] || 0;
    const y = gp.axes[1] || 0;
    if (Math.abs(x) < STICK_DEAD && Math.abs(y) < STICK_DEAD) return null;
    if (Math.abs(x) > Math.abs(y)) return x < 0 ? "left" : "right";
    return y < 0 ? "up" : "down";
  }

  function wasPressed(gp, index) {
    const pressed = !!gp.buttons[index]?.pressed;
    const was = !!prevButtons[index];
    return pressed && !was;
  }

  function handleNativeInput(state) {
    if (!enabled || !state) return;
    if (window.BigPicture?.isOpen?.() && window.BigPicture.handleInput(state)) return;

    if (state.edge?.a) activate();
    if (state.edge?.b) back();
    if (state.edge?.lb) switchTabRel(-1);
    if (state.edge?.rb) switchTabRel(1);

    const dir = state.dir || null;
    const now = Date.now();
    if (dir) {
      if (dir !== stickDirHeld) {
        stickDirHeld = dir;
        stickHeldAt = now;
        navigate(dir);
      } else if (now - stickHeldAt >= STICK_REPEAT_MS) {
        stickHeldAt = now;
        navigate(dir);
      }
    } else {
      stickDirHeld = null;
    }
  }

  function startHttpPoll() {
    if (httpPollId) return;
    httpPollId = window.setInterval(async () => {
      if (!enabled) return;
      try {
        const res = await fetch("/api/gamepad/state", { cache: "no-store" });
        if (!res.ok) return;
        const state = await res.json();
        if (state && state.connected) handleNativeInput(state);
      } catch (_) {}
    }, 33);
  }

  function stopHttpPoll() {
    if (!httpPollId) return;
    window.clearInterval(httpPollId);
    httpPollId = 0;
  }

  function startDetectPoll() {
    if (detectPollId) return;
    detectPollId = window.setInterval(async () => {
      if (enabled) return;
      try {
        const res = await fetch("/api/gamepad/state", { cache: "no-store" });
        if (!res.ok) return;
        const state = await res.json();
        if (state && state.connected && state.active) {
          hooks.onGamepadActivity?.(state);
        }
      } catch (_) {}
    }, 800);
  }

  function stopDetectPoll() {
    if (!detectPollId) return;
    window.clearInterval(detectPollId);
    detectPollId = 0;
  }

  function pollGamepads() {
    if (!enabled) return;
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    const gp = [...pads].find((p) => p && p.connected);
    if (!gp) return;

    if (wasPressed(gp, 0)) activate();
    if (wasPressed(gp, 1)) back();
    if (wasPressed(gp, 4)) switchTabRel(-1);
    if (wasPressed(gp, 5)) switchTabRel(1);

    const dir = dpadDir(gp);
    const now = Date.now();
    if (dir) {
      if (dir !== stickDirHeld) {
        stickDirHeld = dir;
        stickHeldAt = now;
        navigate(dir);
      } else if (now - stickHeldAt >= STICK_REPEAT_MS) {
        stickHeldAt = now;
        navigate(dir);
      }
    } else {
      stickDirHeld = null;
    }

    prevButtons = {};
    gp.buttons.forEach((b, i) => {
      prevButtons[i] = b.pressed;
    });
  }

  function loop() {
    pollGamepads();
    if (enabled) rafId = requestAnimationFrame(loop);
  }

  function startLoop() {
    if (!rafId) rafId = requestAnimationFrame(loop);
  }

  function setEnabled(on) {
    enabled = !!on;
    document.body.classList.toggle("controller-mode", enabled);
    if (!enabled) {
      cancelAnimationFrame(rafId);
      rafId = 0;
      stopHttpPoll();
      startDetectPoll();
      clearFocus();
      stickDirHeld = null;
    } else {
      stopDetectPoll();
      refresh();
      startLoop();
      startHttpPoll();
    }
  }

  function refresh() {
    if (!enabled) return;
    if (focused && !isVisible(focused)) clearFocus();
    if (!focused) focusFirst();
    else if (!collectFocusables().includes(focused)) focusFirst();
  }

  function init(h) {
    Object.assign(hooks, h || {});
    window.addEventListener("gamepadconnected", () => {
      if (enabled) refresh();
      else hooks.onGamepadActivity?.({ connected: true });
    });
    startDetectPoll();
  }

  window.GamepadNav = { init, setEnabled, refresh, handleNativeInput, blur };
})();
