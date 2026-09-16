/* QuickPlay — Big Picture (Steam Deck rehome-style launcher) */
(function () {
  "use strict";

  let entries = [];
  let index = 0;
  let open = false;
  let hooks = {};
  let keyHandler = null;
  let bgSlot = 0;

  function $(id) {
    return document.getElementById(id);
  }

  function decodeHtml(s) {
    if (!s) return "";
    const d = document.createElement("textarea");
    d.innerHTML = s;
    return d.value;
  }

  function displayText(s) {
    return decodeHtml(s || "");
  }

  function coverUrl(entry) {
    if (!entry) return "";
    if (entry.image_url && String(entry.image_url).startsWith("/api/")) {
      return entry.image_url;
    }
    if (entry.entry_id) {
      return `/api/covers/${encodeURIComponent(entry.entry_id)}`;
    }
    return entry.image_url || "";
  }

  function bannerUrl(entry) {
    if (!entry) return "";
    if (entry.banner_url && String(entry.banner_url).startsWith("/api/")) {
      return entry.banner_url;
    }
    if (entry.entry_id) {
      return `/api/banners/${encodeURIComponent(entry.entry_id)}`;
    }
    return coverUrl(entry);
  }

  function preloadAssets(list) {
    list.forEach((entry) => {
      [bannerUrl(entry), coverUrl(entry)].forEach((url) => {
        if (!url) return;
        const img = new Image();
        img.src = url;
      });
    });
  }

  function setBackground(url, fallback) {
    const a = $("bpBgA");
    const b = $("bpBgB");
    if (!a || !b) return;
    const next = bgSlot === 0 ? b : a;
    const prev = bgSlot === 0 ? a : b;
    const target = url || fallback || "";
    if (!target) {
      prev.classList.remove("active");
      next.classList.remove("active");
      return;
    }
    const apply = () => {
      next.classList.add("active");
      prev.classList.remove("active");
    };
    next.onload = apply;
    next.onerror = () => {
      if (fallback && next.src !== fallback) {
        next.src = fallback;
        return;
      }
      apply();
    };
    next.src = target;
    if (next.complete) apply();
    bgSlot = bgSlot === 0 ? 1 : 0;
  }

  function buildSlot(entry, i) {
    const url = coverUrl(entry);
    const title = displayText(entry.title);
    const slot = document.createElement("div");
    slot.className = "bp-slot";
    slot.dataset.index = String(i);

    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "bp-tile";
    tile.setAttribute("aria-label", title);

    const inner = document.createElement("div");
    inner.className = "bp-tile-inner";

    const img = document.createElement("img");
    img.alt = title;
    img.src = url;
    img.draggable = false;
    img.addEventListener("error", () => {
      inner.style.background = "linear-gradient(135deg, #3a3530, #141212)";
    });
    inner.appendChild(img);
    tile.appendChild(inner);

    const mirror = document.createElement("div");
    mirror.className = "bp-mirror";
    mirror.setAttribute("aria-hidden", "true");
    const mirrorImg = document.createElement("img");
    mirrorImg.alt = "";
    mirrorImg.src = url;
    mirrorImg.draggable = false;
    mirror.appendChild(mirrorImg);

    slot.appendChild(tile);
    slot.appendChild(mirror);

    tile.addEventListener("click", () => {
      const idx = parseInt(slot.dataset.index, 10);
      if (idx === index) playCurrent();
      else goTo(idx);
    });

    return slot;
  }

  let centerRaf = 0;
  let scrollFrom = 0;
  let scrollTo = 0;
  let scrollStart = 0;
  const SCROLL_MS = 320;

  function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  function measureFocusOffset() {
    const track = $("bpTrack");
    const wrap = $("bpCarousel");
    if (!track || !wrap) return 0;

    const focus = track.querySelector(".bp-slot.is-focus");
    if (!focus) return 0;

    const wrapW = wrap.clientWidth;
    const focusCenter = focus.offsetLeft + focus.offsetWidth / 2;
    return wrapW / 2 - focusCenter;
  }

  function applyTrackX(x) {
    const track = $("bpTrack");
    if (track) track.style.transform = `translateX(${x}px)`;
  }

  function currentTrackX() {
    const track = $("bpTrack");
    if (!track) return 0;
    const m = /translateX\((-?[\d.]+)px\)/.exec(track.style.transform || "");
    return m ? parseFloat(m[1]) : 0;
  }

  function layoutTrack(immediate) {
    cancelAnimationFrame(centerRaf);
    centerRaf = 0;
    const target = measureFocusOffset();
    if (immediate) {
      applyTrackX(target);
      scrollFrom = scrollTo = target;
      return;
    }

    scrollFrom = currentTrackX();
    scrollTo = target;
    scrollStart = performance.now();

    const tick = (now) => {
      const t = Math.min(1, (now - scrollStart) / SCROLL_MS);
      const x = scrollFrom + (scrollTo - scrollFrom) * easeOutCubic(t);
      applyTrackX(x);
      if (t < 1) centerRaf = requestAnimationFrame(tick);
      else {
        applyTrackX(scrollTo);
        centerRaf = 0;
      }
    };
    centerRaf = requestAnimationFrame(tick);
  }

  function updateSlotStates() {
    const track = $("bpTrack");
    if (!track) return;

    track.querySelectorAll(".bp-slot").forEach((slot) => {
      const i = parseInt(slot.dataset.index, 10);
      const dist = Math.abs(i - index);
      slot.classList.toggle("is-focus", i === index);
      slot.classList.toggle("is-near", dist === 1);
    });
  }

  function renderCarousel() {
    const track = $("bpTrack");
    if (!track) return;
    track.innerHTML = "";
    entries.forEach((entry, i) => track.appendChild(buildSlot(entry, i)));
    updateSlotStates();
    requestAnimationFrame(() => {
      layoutTrack(true);
      requestAnimationFrame(() => layoutTrack(true));
    });
  }

  function updateSelection() {
    updateSlotStates();
    layoutTrack(false);

    const entry = entries[index];
    if (!entry) return;

    const titleEl = $("bpSelectedTitle");
    if (titleEl) {
      const title = displayText(entry.title);
      titleEl.textContent = title;
      titleEl.title = title;
      titleEl.style.animation = "none";
      void titleEl.offsetWidth;
      titleEl.style.animation = "";
    }

    setBackground(bannerUrl(entry), coverUrl(entry));
  }

  function goTo(nextIndex) {
    if (!entries.length) return;
    const clamped = Math.max(0, Math.min(entries.length - 1, nextIndex));
    if (clamped === index) return;
    index = clamped;
    updateSelection();
  }

  function nav(delta) {
    goTo(index + delta);
  }

  function playCurrent() {
    const entry = entries[index];
    if (entry) hooks.onPlay?.(entry.entry_id);
  }

  function onKeyDown(e) {
    if (!open) return;
    if (e.key === "Escape" || e.key === "Backspace") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      nav(-1);
      return;
    }
    if (e.key === "ArrowRight") {
      e.preventDefault();
      nav(1);
      return;
    }
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      playCurrent();
    }
  }

  function handleInput(state) {
    if (!open) return false;
    if (state.edge?.b) {
      close();
      return true;
    }
    if (state.edge?.a) {
      playCurrent();
      return true;
    }
    if (state.dir === "left") {
      nav(-1);
      return true;
    }
    if (state.dir === "right") {
      nav(1);
      return true;
    }
    return true;
  }

  function onResize() {
    if (open) layoutTrack(true);
  }

  function openBigPicture(list, h) {
    hooks = h || {};
    entries = Array.isArray(list) ? list.slice() : [];
    if (!entries.length) return false;

    index = 0;
    open = true;
    preloadAssets(entries);

    const root = $("bigPicture");
    if (!root) return false;
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("active"));
    document.body.classList.add("big-picture-open");

    renderCarousel();
    updateSelection();
    window.addEventListener("resize", onResize);

    keyHandler = onKeyDown;
    document.addEventListener("keydown", keyHandler, true);
    $("bpClose").onclick = () => close();

    return true;
  }

  function close() {
    if (!open) return;
    open = false;
    window.removeEventListener("resize", onResize);
    const root = $("bigPicture");
    if (root) {
      root.classList.remove("active");
      setTimeout(() => root.classList.add("hidden"), 320);
    }
    document.body.classList.remove("big-picture-open");
    if (keyHandler) {
      document.removeEventListener("keydown", keyHandler, true);
      keyHandler = null;
    }
    setBackground("");
    hooks.onClose?.();
    hooks = {};
    entries = [];
  }

  function isOpen() {
    return open;
  }

  window.BigPicture = {
    open: openBigPicture,
    close,
    isOpen,
    handleInput,
    nav,
  };
})();
