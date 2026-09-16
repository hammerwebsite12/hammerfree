/* QuickPlay web UI */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const state = {
  tab: "browse",
  page: 1,
  sort: "views",
  category: "all",
  searchQuery: "",
  games: [],
  library: [],
  tasks: {},
  settings: null,
  config: null,
  categories: [],
  exeModal: { taskId: null, entryId: null, selected: "", mandatory: false, title: "", installDir: "", imageUrl: "" },
  pendingExe: [],
  detailGame: null,
  detailHls: null,
  detailStorage: null,
  libraryHealNotified: false,
};

// ── API helpers ──
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...opts.headers },
    ...opts,
  });
  if (!res.ok) {
    const errText = await res.text();
    let detail = null;
    try {
      const parsed = JSON.parse(errText);
      detail = parsed.detail ?? parsed;
      if (typeof detail === "string") {
        try {
          detail = JSON.parse(detail);
        } catch (_) {
          /* keep string */
        }
      }
    } catch (_) {
      /* plain text error */
    }
    const message = detail?.message || (typeof detail === "string" ? detail : errText) || res.statusText;
    const err = new Error(message);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  return res.json();
}

function setStatus(msg) {
  $("#statusBar").textContent = msg;
}

let toastTimer = null;

function dismissToast() {
  const host = $("#toastHost");
  const toast = host?.querySelector(".toast");
  if (!toast) return;
  toast.classList.remove("show");
  if (toastTimer) {
    clearTimeout(toastTimer);
    toastTimer = null;
  }
  setTimeout(() => {
    if (host) host.innerHTML = "";
  }, 260);
}

function showToast(message, {
  type = "info",
  duration = 5000,
  actionLabel = "",
  onAction = null,
} = {}) {
  const host = $("#toastHost");
  if (!host) {
    setStatus(message);
    return;
  }

  if (toastTimer) clearTimeout(toastTimer);

  const icons = { queue: "⏳", success: "✓", error: "✕", info: "ℹ" };
  host.innerHTML = "";
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span class="toast-icon" aria-hidden="true">${icons[type] || icons.info}</span>
    <span class="toast-message">${escapeHtml(message)}</span>
    ${actionLabel ? `<button type="button" class="toast-action btn small accent">${escapeHtml(actionLabel)}</button>` : ""}
  `;
  host.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("show"));

  if (actionLabel && onAction) {
    toast.querySelector(".toast-action")?.addEventListener("click", () => {
      onAction();
      dismissToast();
    });
  }

  toastTimer = setTimeout(dismissToast, duration);
}

function pulseDownloadsTab() {
  const tab = document.querySelector('.tab[data-tab="downloads"]');
  if (!tab) return;
  tab.classList.remove("tab-pulse");
  void tab.offsetWidth;
  tab.classList.add("tab-pulse");
  setTimeout(() => tab.classList.remove("tab-pulse"), 1400);
}

const CONTACT_LINKS = {
  telegram: {
    label: "@McdaleHammer",
    url: "https://t.me/McdaleHammer",
  },
  discord: {
    // Discord username (not a group invite) — no direct openable URL,
    // so we copy it to the clipboard instead.
    username: "hammersteamsupport",
  },
};

async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

const DEFAULT_LANDING_URL =
  "https://www.facebook.com/profile.php?id=61566938626245";
const LANDING_CLICK_COOLDOWN_MS = 2000;
let lastLandingClickAt = 0;

async function openExternal(url) {
  const target = (url || "").trim();
  if (!target) return;
  if (window.pywebview?.api?.open_url) {
    await window.pywebview.api.open_url(target);
    return;
  }
  window.open(target, "_blank", "noopener,noreferrer");
}

async function openLandingPage() {
  const now = Date.now();
  if (now - lastLandingClickAt < LANDING_CLICK_COOLDOWN_MS) return;
  lastLandingClickAt = now;
  try {
    const data = await api("/api/landing-page");
    await openExternal(data?.url || DEFAULT_LANDING_URL);
  } catch {
    await openExternal(DEFAULT_LANDING_URL);
  }
}

function wireContactLinks() {
  $("#contactTelegram")?.addEventListener("click", () => {
    openExternal(CONTACT_LINKS.telegram.url);
  });
  $("#contactDiscord")?.addEventListener("click", async () => {
    const name = CONTACT_LINKS.discord.username;
    const ok = await copyText(name);
    setStatus(
      ok
        ? `Discord username copied: ${name}`
        : `Discord username: ${name}`,
    );
  });
  $("#followPageBtn")?.addEventListener("click", () => {
    openLandingPage();
  });
}

function formatAnnouncementMessage(text) {
  const lines = String(text || "").split("\n");
  return lines
    .map((line) => {
      let html = escapeHtml(line);
      html = html.replace(
        /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
        (_, label, url) =>
          `<a href="#" class="announcement-link" data-url="${escapeHtml(url)}">${label}</a>`,
      );
      html = html.replace(
        /(https?:\/\/[^\s<]+)/g,
        (url) =>
          `<a href="#" class="announcement-link" data-url="${escapeHtml(url)}">${escapeHtml(url)}</a>`,
      );
      return html;
    })
    .join("<br>");
}

function showAnnouncementModal(message) {
  const modal = $("#announcementModal");
  const body = $("#announcementBody");
  if (!modal || !body) return;
  body.innerHTML = formatAnnouncementMessage(message);
  body.querySelectorAll(".announcement-link").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      openExternal(link.dataset.url || "");
    });
  });
  modal.classList.remove("hidden");
  const close = () => modal.classList.add("hidden");
  $("#announcementOk")?.addEventListener("click", close, { once: true });
  modal.querySelector(".modal-backdrop")?.addEventListener("click", close, { once: true });
}

async function maybeShowStartupAnnouncement() {
  if (state.settings?.disable_announcement_on_startup) return;
  if (state.settings?.allow_big_picture) return;
  try {
    const data = await api("/api/announcement");
    const message = (data?.message || "").trim();
    if (message) showAnnouncementModal(message);
  } catch {
    /* ignore network errors on startup */
  }
}

function loadStorageInfo(path) {
  const el = $("#storageInfo");
  if (!el) return;
  const target = (path || "").trim();
  if (!target) {
    el.textContent = "";
    return;
  }
  const q = new URLSearchParams({ path: target });
  api(`/api/storage?${q}`)
    .then((u) => {
      el.textContent = `Free space: ${formatBytes(u.free_bytes)} / ${formatBytes(u.total_bytes)} total`;
      el.classList.remove("warn", "err");
      if (u.free_bytes < 5 * 1024 ** 3) el.classList.add("warn");
      if (u.free_bytes < 1 * 1024 ** 3) el.classList.add("err");
    })
    .catch(() => {
      el.textContent = "Could not read disk space for this folder.";
    });
}

function renderStorageHint(d) {
  const el = $("#detailStorage");
  if (!el) return;
  const parts = [];
  if (d.store_size_bytes) {
    parts.push(`${t("storage.estSize")}: ${formatBytes(d.store_size_bytes)}`);
  }
  if (d.required_bytes) {
    parts.push(`${t("storage.required")}: ${formatBytes(d.required_bytes)}`);
  }
  if (d.disk_free_bytes) {
    parts.push(`${t("storage.free")}: ${formatBytes(d.disk_free_bytes)}`);
  }
  el.textContent = parts.join(" · ");
  el.className = "detail-storage";
  const blocked = d.required_bytes && d.disk_free_bytes && d.disk_free_bytes < d.required_bytes;
  if (blocked) {
    el.classList.add("warn");
    el.textContent += ` — ${t("storage.insufficient")}`;
  } else if (d.space_unknown && d.disk_free_bytes && d.disk_free_bytes < 10 * 1024 ** 3) {
    el.classList.add("warn");
    el.textContent += ` — ${t("storage.lowFreeUnknown")}`;
  }
}

function canDownloadGame(storage) {
  if (!storage) return true;
  if (storage.required_bytes && storage.disk_free_bytes) {
    return storage.disk_free_bytes >= storage.required_bytes;
  }
  return true;
}

async function showInsufficientSpaceModal(storage, title) {
  const msg = storage?.message || t("storage.blockMsg", {
    title: displayText(title),
    required: formatBytes(storage?.required_bytes || 0),
    free: formatBytes(storage?.disk_free_bytes || 0),
    short: formatBytes(storage?.shortfall_bytes || 0),
  });
  await showConfirm({
    title: t("storage.blockTitle"),
    message: msg,
    confirmText: t("announce.ok"),
    cancelText: "",
    danger: true,
  });
}

function destroyDetailVideo() {
  const vid = $("#detailVideo");
  if (state.detailHls) {
    state.detailHls.destroy();
    state.detailHls = null;
  }
  vid.pause();
  vid.removeAttribute("src");
  vid.load();
  vid.classList.add("hidden");
}

function setupDetailVideo(d) {
  const vid = $("#detailVideo");
  const hero = $("#detailHero");
  destroyDetailVideo();

  const url = (d.video_url || "").trim();
  const type = (d.video_type || "").toLowerCase();
  if (!url) {
    hero.classList.remove("hidden");
    hero.src = d.hero_image || d.image_url || hero.src;
    return;
  }

  const isHls = type === "hls" || url.includes(".m3u8");
  const isMp4 = type === "mp4" || url.includes(".mp4");
  const isWebm = type === "webm" || url.includes(".webm");

  if (isHls && window.Hls && Hls.isSupported()) {
    hero.classList.add("hidden");
    vid.classList.remove("hidden");
    const manifestUrl = `/api/media/hls-manifest?url=${encodeURIComponent(url)}`;
    state.detailHls = new Hls({ enableWorker: true });
    state.detailHls.on(Hls.Events.ERROR, (_e, data) => {
      if (data?.fatal) {
        hero.classList.remove("hidden");
        hero.src = d.hero_image || d.image_url || hero.src;
        vid.classList.add("hidden");
      }
    });
    state.detailHls.loadSource(manifestUrl);
    state.detailHls.attachMedia(vid);
    state.detailHls.on(Hls.Events.MANIFEST_PARSED, () => {
      vid.play().catch(() => {});
    });
    return;
  }

  if (isHls && vid.canPlayType("application/vnd.apple.mpegurl")) {
    hero.classList.add("hidden");
    vid.classList.remove("hidden");
    vid.src = `/api/media/hls-manifest?url=${encodeURIComponent(url)}`;
    vid.play().catch(() => {});
    return;
  }

  if (isMp4 || isWebm) {
    hero.classList.add("hidden");
    vid.classList.remove("hidden");
    vid.src = url;
    vid.play().catch(() => {});
    return;
  }

  hero.classList.remove("hidden");
  hero.src = d.hero_image || d.image_url || hero.src;
}

function formatBytes(n) {
  if (!n || n <= 0) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let s = n;
  let i = 0;
  while (s >= 1024 && i < u.length - 1) { s /= 1024; i++; }
  return i === 0 ? `${s} B` : `${s.toFixed(1)} ${u[i]}`;
}

function formatSpeed(s) {
  return `${formatBytes(s)}/s`;
}

function showLicenseModal(registrationCode, message, deviceFingerprint, isAbnormalHwid, autoCopy = false) {
  const modal = $("#licenseModal");
  const codeEl = $("#licenseCode");
  const fpEl = $("#licenseFp");
  const fpLabel = $("#licenseFpLabel");
  const msgEl = $("#licenseMessage");
  const statusEl = $("#licenseCopyStatus");
  const copyBtn = $("#licenseCopy");
  const copyFpBtn = $("#licenseCopyFp");
  const copyAllBtn = $("#licenseCopyAll");
  if (!modal || !codeEl) return;

  const showFp = Boolean(isAbnormalHwid && deviceFingerprint);
  codeEl.value = registrationCode || "";
  if (fpEl) fpEl.value = showFp ? deviceFingerprint : "";
  fpEl?.classList.toggle("hidden", !showFp);
  fpLabel?.classList.toggle("hidden", !showFp);
  copyFpBtn?.classList.toggle("hidden", !showFp);
  copyAllBtn?.classList.toggle("hidden", !showFp);

  if (msgEl) {
    if (message) {
      msgEl.textContent = message;
    } else if (showFp) {
      msgEl.textContent = t("license.customOsMessage");
    } else {
      msgEl.textContent = t("license.message");
    }
  }
  if (statusEl) {
    statusEl.textContent = "";
    statusEl.classList.add("hidden");
  }
  modal.classList.remove("hidden");

  const close = () => modal.classList.add("hidden");
  const closeBtn = $("#licenseClose");
  const backdrop = modal.querySelector(".modal-backdrop");

  const notifyCopied = () => {
    if (statusEl) {
      statusEl.textContent = t("license.copied");
      statusEl.classList.remove("hidden");
    }
  };

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(codeEl.value);
      notifyCopied();
    } catch (_) {
      codeEl.focus();
      codeEl.select();
    }
  };

  const onCopyFp = async () => {
    if (!fpEl?.value) return;
    try {
      await navigator.clipboard.writeText(fpEl.value);
      notifyCopied();
    } catch (_) {
      fpEl.focus();
      fpEl.select();
    }
  };

  const onCopyAll = async () => {
    const text = `Registration Code: ${codeEl.value}\nDevice Fingerprint: ${fpEl?.value || ""}`;
    try {
      await navigator.clipboard.writeText(text);
      notifyCopied();
    } catch (_) {
      codeEl.focus();
      codeEl.select();
    }
  };

  copyBtn?.addEventListener("click", onCopy, { once: true });
  copyFpBtn?.addEventListener("click", onCopyFp, { once: true });
  copyAllBtn?.addEventListener("click", onCopyAll, { once: true });
  closeBtn?.addEventListener("click", close, { once: true });
  backdrop?.addEventListener("click", close, { once: true });

  if (autoCopy) {
    void onCopy();
  }
}

function showConfirm({ title, message, confirmText = "OK", cancelText = "Cancel", danger = false }) {
  return new Promise((resolve) => {
    const modal = $("#confirmModal");
    $("#confirmTitle").textContent = title;
    $("#confirmMessage").textContent = message;
    const okBtn = $("#confirmOk");
    const cancelBtn = $("#confirmCancel");
    okBtn.textContent = confirmText;
    cancelBtn.textContent = cancelText;
    okBtn.classList.toggle("danger", danger);
    modal.classList.remove("hidden");
    // Focus Cancel (not the destructive action) so a stray Enter/Space or a
    // momentum click right after opening the dialog can't confirm a delete.
    setTimeout(() => cancelBtn.focus(), 0);

    const cleanup = (result) => {
      modal.classList.add("hidden");
      okBtn.removeEventListener("click", onOk);
      cancelBtn.removeEventListener("click", onCancel);
      document.removeEventListener("keydown", onKey);
      modal.querySelector(".modal-backdrop").removeEventListener("click", onCancel);
      resolve(result);
    };
    const onOk = () => cleanup(true);
    const onCancel = () => cleanup(false);
    const onKey = (e) => {
      if (e.key === "Escape") onCancel();
    };
    okBtn.addEventListener("click", onOk);
    cancelBtn.addEventListener("click", onCancel);
    document.addEventListener("keydown", onKey);
    modal.querySelector(".modal-backdrop").addEventListener("click", onCancel);
  });
}

function formatEta(dl, total, speed) {
  if (!total || !speed || dl >= total) return "--:--";
  const sec = Math.floor((total - dl) / speed);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 59 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

// ── Tabs ──
function switchTab(name) {
  state.tab = name;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
  if (name === "library") { loadLibrary(); loadPendingExe(); }
  if (name === "downloads") { loadTasks(); loadPendingExe(); loadLogs(); }
  if (name === "settings") loadSettingsForm();
  gpRefresh();
}

function gpRefresh() {
  window.GamepadNav?.refresh();
}

$$(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

// ── Browse ──
function createGameCard(game, { showHint = true, onClick } = {}) {
  const el = document.createElement("div");
  el.className = "game-item";
  el.innerHTML = `
    <div class="game-media">
      <div class="skeleton"></div>
      ${showHint ? `<span class="download-hint">${t("browse.viewGame")}</span>` : ""}
      <img loading="lazy" data-src="${escapeAttr(game.image_url)}" alt="${escapeAttr(displayText(game.title))}" />
    </div>
    <div class="game-info"><span title="${escapeAttr(displayText(game.title))}">${text(game.title)}</span></div>
  `;
  const img = el.querySelector("img");
  const skeleton = el.querySelector(".skeleton");
  const media = el.querySelector(".game-media");
  img.addEventListener("load", () => skeleton.classList.add("hidden"));
  img.addEventListener("error", () => {
    img.classList.add("hidden");
    skeleton.classList.remove("hidden");
    skeleton.classList.add("cover-fallback");
    if (media) media.classList.add("cover-missing");
  });
  if (game.image_url) img.src = game.image_url;
  else {
    skeleton.classList.remove("hidden");
    skeleton.classList.add("cover-fallback");
    if (media) media.classList.add("cover-missing");
  }

  if (onClick) el.addEventListener("click", onClick);
  return el;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function decodeHtml(s) {
  if (!s) return "";
  const d = document.createElement("textarea");
  d.innerHTML = s;
  return d.value;
}

function text(s) {
  return escapeHtml(decodeHtml(s));
}

function displayText(s) {
  return decodeHtml(s);
}

function escapeAttr(s) {
  return (s || "").replace(/"/g, "&quot;");
}

function isInLibrary(game) {
  if (!game || !state.library) return false;
  const gid = String(game.game_id || "");
  return state.library.some((e) => String(e.game_id || "") === gid);
}

function renderBrowseGrid(games) {
  const grid = $("#gameGrid");
  grid.innerHTML = "";
  $("#browseEmpty").classList.toggle("hidden", games.length > 0);
  games.forEach((game) => {
    const card = createGameCard(game, {
      onClick: () => openGameDetail(game),
    });
    if (isInLibrary(game)) {
      card.classList.add("in-library");
      const media = card.querySelector(".game-media");
      if (media) {
        const badge = document.createElement("span");
        badge.className = "library-badge";
        badge.textContent = "In Library";
        media.appendChild(badge);
      }
    }
    grid.appendChild(card);
  });
  gpRefresh();
}

async function reloadCategories() {
  const cats = await api("/api/categories");
  state.categories = cats.categories || [];
  renderCategoryBar();
}

async function applyStoreSwitchFromResponse(data) {
  if (!data || !data.store_switched) return;
  state.settings = { ...state.settings, store: data.store };
  await reloadCategories();
  populateStoreSelect();
  const reason =
    data.store_switch_reason === "search_no_results"
      ? t("status.storeAutoSearch")
      : t("status.storeAutoBrowse");
  setStatus(t("status.storeSwitched", { server: data.store_label || data.store, reason }));
}

async function loadBrowse() {
  $("#browseLoading").classList.remove("hidden");
  $("#gameGrid").innerHTML = "";
  try {
    let data;
    if (state.searchQuery) {
      data = await api(`/api/search?q=${encodeURIComponent(state.searchQuery)}`);
      $("#pageLabel").textContent = `Search: "${state.searchQuery}"`;
    } else {
      data = await api(
        `/api/browse?page=${state.page}&sort=${state.sort}&category=${encodeURIComponent(state.category)}`
      );
      const catLabel = state.categories.find((c) => c.id === state.category)?.label || state.category;
      $("#pageLabel").textContent = `${t("pager.page")} ${state.page} · ${state.sort} · ${catLabel}`;
    }
    await applyStoreSwitchFromResponse(data);
    state.games = data.games;
    renderBrowseGrid(data.games);
    if (!data.store_switched) {
      setStatus(`${data.games.length} games loaded.`);
    }
  } catch (e) {
    setStatus(`Error: ${e.message}`);
  } finally {
    $("#browseLoading").classList.add("hidden");
  }
}

function renderCategoryBar() {
  const bar = $("#categoryBar");
  bar.innerHTML = "";
  state.categories.forEach((cat) => {
    const chip = document.createElement("button");
    chip.className = "category-chip" + (state.category === cat.id ? " active" : "");
    chip.textContent = cat.label;
    chip.addEventListener("click", () => {
      state.category = cat.id;
      state.searchQuery = "";
      state.page = 1;
      $$(".category-chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      loadBrowse();
    });
    bar.appendChild(chip);
  });
  gpRefresh();
}

$("#searchBtn").addEventListener("click", () => {
  state.searchQuery = $("#searchInput").value.trim();
  state.page = 1;
  loadBrowse();
});

$("#searchInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { state.searchQuery = e.target.value.trim(); state.page = 1; loadBrowse(); }
});

$$(".sort-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".sort-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.sort = btn.dataset.sort;
    state.searchQuery = "";
    state.page = 1;
    loadBrowse();
  });
});

// ── Settings ──
function applyLogPanelVisibility() {
  $("#logPanel")?.classList.remove("collapsed");
}

async function loadSettings() {
  state.settings = await api("/api/settings");
  applyLogPanelVisibility();
}

function populateLanguageSelect() {
  const sel = $("#languageSelect");
  if (!sel) return;
  const langs = state.settings?.supported_languages || ["en", "zh", "es", "tl"];
  if (sel.options.length !== langs.length) {
    sel.innerHTML = langs
      .map((code) => `<option value="${code}">${I18N_LANG_NAMES[code] || code}</option>`)
      .join("");
  }
  sel.value = state.settings?.language || "en";
}

function populateStoreSelect() {
  const sel = $("#storeSelect");
  if (!sel) return;
  const options = state.settings?.store_options || [
    { id: "server1", label: "Server 1" },
    { id: "server2", label: "Server 2" },
  ];
  sel.innerHTML = options.map((o) => `<option value="${o.id}">${o.label}</option>`).join("");
  sel.value = state.settings?.store || "server1";
}

function loadSettingsForm() {
  const s = state.settings;
  if (!s) return;
  const verEl = $("#appVersionLabel");
  if (verEl) {
    verEl.textContent = t("settings.version", { version: s.app_version || state.config?.version || "2.2" });
  }
  populateLanguageSelect();
  populateStoreSelect();
  $("#downloadDir").value = s.download_dir || "";
  $("#connectionsRange").value = s.connections || 8;
  $("#connectionsValue").textContent = s.connections || 8;
  $("#verificationWindowFullCheck").checked = s.verification_window_full === true;
  $("#controllerEnabledCheck").checked = s.controller_enabled === true;
  $("#allowBigPictureCheck").checked = s.allow_big_picture === true;
  $("#disableAnnouncementCheck").checked = s.disable_announcement_on_startup === true;
  $("#defenderCheck").checked = s.defender_exclusion !== false;
  updateDefenderStatus(s.defender_status || "");
  const libHint = $("#libraryPathHint");
  if (libHint) {
    const lp = s.library_path || state.libraryPath || "";
    libHint.textContent = lp ? t("settings.libraryPath", { path: lp }) : "";
  }
  if (state.config && !state.config.is_windows) {
    $("#defenderGroup").classList.add("hidden");
  }
  loadStorageInfo(s.download_dir || "");
}

function updateDefenderStatus(msg) {
  const el = $("#defenderStatus");
  el.textContent = msg || "";
  el.className = "setting-hint";
  if (!msg) return;
  if (msg.toLowerCase().includes("added") || msg.toLowerCase().includes("already")) {
    el.classList.add("ok");
  } else if (
    msg.toLowerCase().includes("administrator")
    || msg.toLowerCase().includes("disabled")
    || msg.toLowerCase().includes("add it manually")
    || msg.toLowerCase().includes("windows security")
  ) {
    el.classList.add("warn");
    el.style.whiteSpace = "pre-wrap";
  } else if (msg.toLowerCase().includes("failed") || msg.toLowerCase().includes("not available")) {
    el.classList.add("err");
    el.style.whiteSpace = "pre-wrap";
  }
}

async function pickFolderNative() {
  const initial = $("#downloadDir").value || "";
  if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder) {
    return await window.pywebview.api.pick_folder(initial);
  }
  return prompt("Enter download folder path:", initial) || "";
}

$("#connectionsRange").addEventListener("input", (e) => {
  $("#connectionsValue").textContent = e.target.value;
});

$("#browseFolderBtn").addEventListener("click", async () => {
  const path = await pickFolderNative();
  if (!path) return;
  $("#downloadDir").value = path;
  loadStorageInfo(path);
  try {
    state.settings = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ download_dir: path }),
    });
    updateDefenderStatus(state.settings.defender_status || "");
    $("#downloadDir").value = state.settings.download_dir || path;
    $("#rootHint").textContent = t("status.gamesFolder", { dir: state.settings.download_dir });
    setStatus(t("status.folderSet", { dir: state.settings.download_dir }));
  } catch (e) {
    setStatus(`Failed to set folder: ${e.message}`);
  }
});

async function applyStoreChange(newStore, { reloadBrowse = true } = {}) {
  const oldStore = state.settings?.store;
  if (!newStore || newStore === oldStore) return false;

  state.settings = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ store: newStore }),
  });
  populateStoreSelect();
  closeGameDetail();
  state.page = 1;
  state.searchQuery = "";
  state.category = "all";
  renderCategoryBar();
  await reloadCategories();
  if (reloadBrowse) {
    if (state.tab !== "browse") switchTab("browse");
    await loadBrowse();
  }
  setStatus(t("status.settingsSaved"));
  return true;
}

$("#storeSelect")?.addEventListener("change", async (e) => {
  const target = e.target.value;
  if (target === state.settings?.store) return;
  try {
    await applyStoreChange(target);
  } catch (err) {
    populateStoreSelect();
    setStatus(`Settings error: ${err.message}`);
  }
});

$("#saveSettingsBtn").addEventListener("click", async () => {
  try {
    const body = {
      download_dir: $("#downloadDir").value.trim(),
      connections: parseInt($("#connectionsRange").value, 10),
      verification_window_full: $("#verificationWindowFullCheck").checked,
      controller_enabled: $("#controllerEnabledCheck").checked,
      allow_big_picture: $("#allowBigPictureCheck").checked,
      disable_announcement_on_startup: $("#disableAnnouncementCheck").checked,
      defender_exclusion: $("#defenderCheck").checked,
      store: $("#storeSelect").value,
    };
    const oldStore = state.settings?.store;
    state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
    applyLogPanelVisibility();
    window.GamepadNav?.setEnabled(!!state.settings.controller_enabled);
    updateDefenderStatus(state.settings.defender_status || "");
    if (state.settings.store !== oldStore) {
      closeGameDetail();
      state.page = 1;
      state.searchQuery = "";
      state.category = "all";
      renderCategoryBar();
      await reloadCategories();
      if (state.tab !== "browse") switchTab("browse");
      await loadBrowse();
    }
    if (state.config) {
      $("#rootHint").textContent = t("status.gamesFolder", { dir: state.settings.download_dir });
    }
    setStatus(t("status.settingsSaved"));
  } catch (e) {
    setStatus(`Settings error: ${e.message}`);
  }
});

$("#allowBigPictureCheck")?.addEventListener("change", () => {
  if (state.settings) {
    state.settings.allow_big_picture = $("#allowBigPictureCheck").checked;
  }
});

$("#controllerEnabledCheck")?.addEventListener("change", async (e) => {
  const on = e.target.checked;
  window.GamepadNav?.setEnabled(on);
  try {
    state.settings = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ controller_enabled: on }),
    });
  } catch (_) {}
});

$("#languageSelect")?.addEventListener("change", async (e) => {
  const lang = e.target.value;
  try {
    state.settings = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ language: lang }),
    });
  } catch (_) {}
  i18nSetLang((state.settings && state.settings.language) || lang);
  applyI18n();
  // Re-render dynamic content that isn't tagged with data-i18n.
  if (state.library) renderLibrary();
  if (state.tab === "downloads") renderTasks(); else renderDownloadDock();
  const dir = state.settings?.download_dir;
  if (dir) $("#rootHint").textContent = t("status.gamesFolder", { dir });
  setStatus(t("status.settingsSaved"));
});

$("#prevPage").addEventListener("click", () => {
  if (state.page > 1) { state.searchQuery = ""; state.page--; loadBrowse(); }
});

$("#nextPage").addEventListener("click", () => {
  state.searchQuery = "";
  state.page++;
  loadBrowse();
});

// ── Game store detail ──
function closeGameDetail() {
  $("#gameDetailModal").classList.add("hidden");
  destroyDetailVideo();
  state.detailGame = null;
  gpRefresh();
}

async function openGameDetail(game) {
  state.detailGame = game;
  $("#gameDetailModal").classList.remove("hidden");
  $("#detailLoading").classList.remove("hidden");
  $("#detailTitle").textContent = displayText(game.title);
  $("#detailDesc").textContent = "";
  $("#detailDescHtml").innerHTML = "";
  $("#detailMeta").textContent = "";
  $("#detailTags").innerHTML = "";
  $("#detailScreenshots").innerHTML = "";
  $("#screenshotsTitle").classList.add("hidden");
  $("#detailSource").textContent = "";
  $("#detailStorage").textContent = "";
  $("#detailHero").src = game.image_url || "";
  $("#detailHero").classList.remove("hidden");

  try {
    const q = new URLSearchParams({
      title: game.title,
      image_url: game.image_url || "",
    });
    const d = await api(`/api/games/${encodeURIComponent(game.game_id)}/details?${q}`);
    state.detailStorage = {
      store_size_bytes: d.store_size_bytes,
      disk_free_bytes: d.disk_free_bytes,
      required_bytes: d.required_bytes,
      space_ok: d.space_ok,
      space_unknown: d.space_unknown,
      message: d.message,
    };
    renderGameDetail(d);
    setStatus(d.title);
  } catch (e) {
    $("#detailDesc").textContent = "Could not load game details.";
    setStatus(`Detail error: ${e.message}`);
  } finally {
    $("#detailLoading").classList.add("hidden");
  }
  gpRefresh();
}

function renderGameDetail(d) {
  $("#detailTitle").textContent = displayText(d.title || state.detailGame?.title || "");

  const metaParts = [];
  if (d.developer) metaParts.push(d.developer);
  if (d.release_date) metaParts.push(d.release_date);
  let metaHtml = metaParts.join(" · ");
  if (d.review_score != null && d.review_count) {
    metaHtml += `<span class="review">${d.review_score}% (${d.review_count.toLocaleString()} reviews)</span>`;
  }
  $("#detailMeta").innerHTML = metaHtml;

  $("#detailTags").innerHTML = (d.genres || [])
    .map((g) => `<span class="detail-tag">${escapeHtml(g)}</span>`)
    .join("");

  setupDetailVideo(d);

  renderStorageHint(d);

  if (d.description_html && d.source !== "steam") {
    $("#detailDescHtml").innerHTML = d.description_html;
  } else {
    $("#detailDescHtml").innerHTML = "";
  }
  if (d.source === "steam" || d.source === "mixed" || !d.description_html) {
    $("#detailDesc").textContent = d.description || "";
  } else {
    $("#detailDesc").textContent = "";
  }

  const shots = d.screenshots || [];
  const shotsEl = $("#detailScreenshots");
  shotsEl.innerHTML = "";
  if (shots.length) {
    $("#screenshotsTitle").classList.remove("hidden");
    let main = shotsEl.parentElement.querySelector(".detail-screenshot-main");
    if (!main) {
      main = document.createElement("img");
      main.className = "detail-screenshot-main";
      shotsEl.parentElement.insertBefore(main, shotsEl);
    }
    main.src = shots[0].full;
    main.classList.add("visible");
    shots.forEach((s, i) => {
      const thumb = document.createElement("img");
      thumb.src = s.thumb || s.full;
      thumb.loading = "lazy";
      thumb.alt = `Screenshot ${i + 1}`;
      if (i === 0) thumb.classList.add("active");
      thumb.addEventListener("click", () => {
        main.src = s.full;
        shotsEl.querySelectorAll("img").forEach((t) => t.classList.remove("active"));
        thumb.classList.add("active");
      });
      shotsEl.appendChild(thumb);
    });
  } else {
    $("#screenshotsTitle").classList.add("hidden");
    const oldMain = shotsEl.parentElement.querySelector(".detail-screenshot-main");
    if (oldMain) oldMain.remove();
  }

  $("#detailSource").textContent = "";

  updateDetailLibraryState();
}

function updateDetailLibraryState() {
  const btn = $("#detailDownload");
  const note = $("#detailLibraryNote");
  const inLib = isInLibrary(state.detailGame);
  if (note) note.classList.toggle("hidden", !inLib);
  if (btn) {
    btn.textContent = inLib ? "\u2b07 Re-download" : "\u2b07 Download Game";
    btn.classList.toggle("in-library", inLib);
    const blocked = !canDownloadGame(state.detailStorage);
    btn.disabled = blocked;
    btn.title = blocked ? (t("storage.blockTitle") || "") : "";
  }
}

$("#detailClose").addEventListener("click", closeGameDetail);
$("#detailDownload").addEventListener("click", async () => {
  if (!state.detailGame) return;
  const g = state.detailGame;
  const st = state.detailStorage;
  if (!canDownloadGame(st)) {
    await showInsufficientSpaceModal(st, g.title);
    return;
  }
  if (st?.space_unknown) {
    const ok = await showConfirm({
      title: t("storage.unknownTitle"),
      message: t("storage.unknownMsg", {
        free: formatBytes(st.disk_free_bytes || 0),
      }),
      confirmText: t("storage.continueAnyway"),
      cancelText: t("confirm.cancel"),
      danger: true,
    });
    if (!ok) return;
  }
  closeGameDetail();
  startDownload(g, st);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#gameDetailModal").classList.contains("hidden")) {
    closeGameDetail();
  }
});

// ── Download ──
async function startDownload(game, storage = null) {
  const title = displayText(game.title);
  try {
    const data = await api("/api/downloads", {
      method: "POST",
      body: JSON.stringify({
        game_id: game.game_id,
        title: game.title,
        image_url: game.image_url,
        store_size_bytes: storage?.store_size_bytes ?? null,
      }),
    });

    if (data.already_queued) {
      const msg = t("download.alreadyInQueue", { title });
      setStatus(msg);
      showToast(msg, {
        type: "info",
        actionLabel: t("download.viewQueue"),
        onAction: () => switchTab("downloads"),
      });
    } else if (data.queued && data.queue_position) {
      const msg = t("download.addedToQueue", { title, position: data.queue_position });
      setStatus(msg);
      showToast(msg, {
        type: "queue",
        duration: 6000,
        actionLabel: t("download.viewQueue"),
        onAction: () => switchTab("downloads"),
      });
      pulseDownloadsTab();
    } else {
      const msg = t("download.started", { title });
      setStatus(msg);
      showToast(msg, { type: "success", duration: 4500 });
      pulseDownloadsTab();
    }

    loadTasks();
  } catch (e) {
    if (e.status === 507 && e.detail) {
      await showInsufficientSpaceModal(e.detail, title);
      setStatus(e.detail.message || t("storage.insufficient"));
      return;
    }
    const msg = t("download.failed", { msg: e.message });
    setStatus(msg);
    showToast(msg, { type: "error", duration: 6000 });
  }
}

// ── Tasks ──
function isTaskActive(t) {
  if (t.cancelled) return false;
  if (t.state === "Cancelled") return false;
  if ((t.status_message || "").toLowerCase() === "cancelled") return false;
  if (t.phase === "done" && t.state === "Completed") return false;
  if (t.phase === "queued") return true;
  if (t.phase === "rate_limit" || t.phase === "resolving") return true;
  if (t.phase === "extract") return true;
  if (t.phase === "download") return true;
  if (t.phase === "error") return true;
  return ["Queued", "Connecting", "Allocating", "Downloading", "Merging", "Paused", "Waiting"].includes(t.state);
}

function pickDockTask(tasks) {
  const active = tasks.filter(isTaskActive);
  if (!active.length) return null;

  const rank = (t) => {
    const phase = t.phase || "";
    const st = t.state || "";
    if (phase === "extract") return 0;
    if (phase === "resolving" || phase === "rate_limit") return 1;
    if (phase === "download") {
      if (["Downloading", "Connecting", "Allocating", "Merging"].includes(st)) return 2;
      if (st === "Paused") return 6;
      return 3;
    }
    if (phase === "error" || phase === "license_required") return 7;
    if (phase === "queued") return 8;
    return 9;
  };

  active.sort((a, b) => {
    const d = rank(a) - rank(b);
    if (d !== 0) return d;
    return (Number(b.speed) || 0) - (Number(a.speed) || 0);
  });
  return active[0];
}

function taskProgressPct(t) {
  if (t.phase === "extract" || t.phase === "done") {
    return t.extract_total > 0 ? Math.min(100, (t.extract_current / t.extract_total) * 100) : 0;
  }
  return t.total_size > 0 ? Math.min(100, (t.downloaded / t.total_size) * 100) : 0;
}

function taskStatusLine(t) {
  if (t.phase === "license_required") {
    return t.status_message || t("license.title");
  }
  if (t.phase === "resolving" || t.phase === "rate_limit") {
    if (t.status_message) return t.status_message;
    if (t.link_refresh) return t("download.refreshLinkProgress") || "Refreshing download link…";
    return t("download.resolvingLink") || "Resolving download link…";
  }
  if (t.verify_countdown && t.rate_limit_seconds > 0) {
    return t.status_message || `Preparing download — ${t.rate_limit_seconds}s`;
  }
  if (t.verify_countdown && t.phase === "resolving") {
    return t.status_message || "Preparing download verification...";
  }
  if (t.rate_limit_seconds > 0) {
    return t.status_message || `Rate limit — retry in ${t.rate_limit_seconds}s`;
  }
  return t.status_message || t.state;
}

function canRetryTask(t) {
  return t.phase === "rate_limit" || t.phase === "error" || t.phase === "license_required" || (t.phase === "resolving" && t.rate_limit_seconds > 0);
}

function canRefreshDownloadLink(t) {
  if (t.phase !== "download") return false;
  return ["Queued", "Connecting", "Allocating", "Downloading", "Merging", "Paused", "Error"].includes(t.state);
}

function buildTaskControls(task, { compact = false } = {}) {
  const isQueued = task.phase === "queued";
  const canControl = !isQueued && ["Queued", "Connecting", "Allocating", "Downloading", "Merging", "Paused"].includes(task.state);
  const isResolving = task.phase === "resolving" || task.phase === "rate_limit";
  const showRetry = canRetryTask(task);
  const showRefresh = canRefreshDownloadLink(task);
  const cls = compact ? "btn small" : "btn small";
  let html = "";
  if (isQueued) {
    html += `<button type="button" class="${cls} danger cancel-btn">${t("download.cancel")}</button>`;
    return html;
  }
  if (showRetry) {
    html += `<button type="button" class="${cls} accent retry-btn">${t("download.retry") || "Retry"}</button>`;
  }
  if (showRefresh) {
    const refreshLabel = t("download.refreshLink") || "Refresh link";
    html += `<button type="button" class="${cls} refresh-link-btn" title="${escapeAttr(t("download.refreshLinkHint") || refreshLabel)}">${escapeHtml(refreshLabel)}</button>`;
  }
  if (canControl && !isResolving) {
    html += `<button type="button" class="${cls} pause-btn">${task.state === "Paused" ? (t("download.resume") || "Resume") : (t("download.pause") || "Pause")}</button>`;
  }
  if (canControl || isResolving || showRetry) {
    html += `<button type="button" class="${cls} danger cancel-btn">${t("download.cancel") || "Cancel"}</button>`;
  }
  return html;
}

function sortTasksForDisplay(tasks) {
  const active = tasks.filter((task) => task.phase !== "queued");
  const queued = tasks.filter((task) => task.phase === "queued");
  const phasePriority = { download: 0, extract: 1, resolving: 2, rate_limit: 3, error: 4, license_required: 5 };
  const stateBoost = (t) => {
    if (t.phase !== "download") return 0;
    if (["Downloading", "Connecting", "Allocating", "Merging"].includes(t.state)) return 0;
    if (t.state === "Paused") return 1;
    return 0;
  };
  active.sort((a, b) => {
    const d = (phasePriority[a.phase] ?? 9) - (phasePriority[b.phase] ?? 9);
    if (d !== 0) return d;
    const s = stateBoost(a) - stateBoost(b);
    if (s !== 0) return s;
    return (Number(b.speed) || 0) - (Number(a.speed) || 0);
  });
  queued.sort((a, b) => (a.queue_position || 999) - (b.queue_position || 999));
  return { active, queued };
}

function getTaskById(taskId) {
  return state.tasks[taskId] || null;
}

let _pauseActionBusy = false;
const _cancelInFlight = new Set();
let _tasksRenderTimer = null;

function isConfirmOpen() {
  const modal = $("#confirmModal");
  return modal && !modal.classList.contains("hidden");
}

function scheduleRenderTasks() {
  if (state.tab !== "downloads") return;
  if (_tasksRenderTimer) return;
  _tasksRenderTimer = setTimeout(() => {
    _tasksRenderTimer = null;
    if (isConfirmOpen()) {
      scheduleRenderTasks();
      return;
    }
    renderTasks();
  }, 180);
}

async function onDownloadTaskAction(e) {
  const btn = e.target.closest("button");
  if (!btn || btn.id === "dockOpenDownloads") return;

  const host = btn.closest("[data-task-id]");
  if (!host) return;
  const task = getTaskById(host.dataset.taskId);
  if (!task) return;

  e.preventDefault();
  e.stopPropagation();

  if (btn.classList.contains("cancel-btn")) {
    await cancelDownloadTask(task);
    return;
  }
  if (btn.classList.contains("pause-btn")) {
    if (_pauseActionBusy) return;
    await togglePauseDownloadTask(task);
    return;
  }
  if (btn.classList.contains("retry-btn")) {
    if (_pauseActionBusy) return;
    _pauseActionBusy = true;
    btn.disabled = true;
    try {
      await api(`/api/downloads/${encodeURIComponent(task.task_id)}/retry`, { method: "POST" });
      await loadTasks();
    } catch (err) {
      setStatus(`Retry failed: ${err.message}`);
    } finally {
      _pauseActionBusy = false;
    }
    return;
  }
  if (btn.classList.contains("refresh-link-btn")) {
    if (_pauseActionBusy) return;
    _pauseActionBusy = true;
    btn.disabled = true;
    const optimistic = {
      ...task,
      phase: "resolving",
      state: "Connecting",
      link_refresh: true,
      speed: 0,
      error: "",
      rate_limit_seconds: 0,
      status_message: t("download.refreshLinkProgress") || "Refreshing download link — please wait...",
    };
    state.tasks[task.task_id] = optimistic;
    renderTasks();
    renderDownloadDock(true);
    try {
      await api(`/api/downloads/${encodeURIComponent(task.task_id)}/refresh-link`, { method: "POST" });
      setStatus(t("download.refreshLinkStarted", { title: displayText(task.title) }) || `Refreshing link for ${displayText(task.title)}…`);
      await loadTasks();
    } catch (err) {
      setStatus(t("download.refreshLinkFailed", { msg: err.message }) || `Refresh link failed: ${err.message}`);
    } finally {
      _pauseActionBusy = false;
    }
  }
}

async function cancelDownloadTask(task) {
  if (_cancelInFlight.has(task.task_id)) return;

  const isQueued = task.phase === "queued";
  const ok = await showConfirm({
    title: isQueued ? (t("download.removeQueuedTitle") || "Remove from queue?") : t("download.cancelTitle"),
    message: isQueued
      ? (t("download.removeQueuedMsg", { title: displayText(task.title) }) || `Remove "${displayText(task.title)}" from the download queue?`)
      : t("download.cancelMsg", { title: displayText(task.title) }),
    confirmText: isQueued ? (t("download.removeQueuedConfirm") || "Remove") : t("download.cancelConfirm"),
    cancelText: t("confirm.cancel"),
    danger: true,
  });
  if (!ok) return;

  _cancelInFlight.add(task.task_id);
  try {
    await api("/api/downloads/cancel", {
      method: "POST",
      body: JSON.stringify({ task_id: task.task_id }),
    });
    delete state.tasks[task.task_id];
    renderTasks();
    renderDownloadDock(true);
    setStatus(t("download.cancelled", { title: displayText(task.title) }));
  } catch (e) {
    setStatus(t("download.cancelFailed", { msg: e.message }));
    loadTasks();
  } finally {
    _cancelInFlight.delete(task.task_id);
  }
}

async function togglePauseDownloadTask(task) {
  const taskId = task.task_id;
  const current = state.tasks[taskId] || task;
  const isPaused = current.state === "Paused";

  if (_pauseActionBusy) return;
  _pauseActionBusy = true;

  const prev = { ...current };
  const nextState = isPaused ? "Downloading" : "Paused";
  state.tasks[taskId] = {
    ...current,
    state: nextState,
    status_message: isPaused ? "Downloading" : (t("download.pausedQueueHint") || "Paused — next in queue can start"),
    speed: isPaused ? current.speed : 0,
  };
  renderTasks();
  renderDownloadDock(true);

  try {
    await api(`/api/downloads/${encodeURIComponent(taskId)}/${isPaused ? "resume" : "pause"}`, { method: "POST" });
    await loadTasks();
  } catch (e) {
    state.tasks[taskId] = prev;
    renderTasks();
    renderDownloadDock(true);
    setStatus(t("download.pauseFailed", { msg: e.message }));
  } finally {
    _pauseActionBusy = false;
  }
}

function wireTaskButtons(card, task) {
  // Kept for compatibility; actions use delegated handlers on taskList/downloadDock.
  void card;
  void task;
}

function wireDownloadTaskActions() {
  const handler = (e) => {
    if (e.target.closest("#dockOpenDownloads")) {
      switchTab("downloads");
      return;
    }
    onDownloadTaskAction(e);
  };
  $("#taskList")?.addEventListener("click", handler);
  $("#downloadDock")?.addEventListener("click", handler);
}

function renderDownloadDock(force = false) {
  const dock = $("#downloadDock");
  const inner = $("#downloadDockInner");
  if (!dock || !inner) return;

  const task = pickDockTask(Object.values(state.tasks));
  if (!task) {
    dock.classList.add("hidden");
    document.body.classList.remove("has-download-dock");
    inner.innerHTML = "";
    delete inner.dataset.dockTaskId;
    delete inner.dataset.dockTaskState;
    delete inner.dataset.dockTaskPhase;
    return;
  }

  dock.classList.remove("hidden");
  document.body.classList.add("has-download-dock");

  const sameTask =
    !force &&
    inner.dataset.dockTaskId === task.task_id &&
    inner.dataset.dockTaskState === String(task.state || "") &&
    inner.dataset.dockTaskPhase === String(task.phase || "");

  // Patch progress in place so Pause/Resume buttons aren't destroyed mid-click
  // by high-frequency SSE progress updates.
  if (sameTask && inner.querySelector(".dock-actions")) {
    patchDockProgress(inner, task);
    return;
  }

  const pct = taskProgressPct(task);
  const isQueued = task.phase === "queued";
  const isWait = task.phase === "resolving" || task.phase === "rate_limit";
  const waitSecs = task.rate_limit_seconds || 0;
  const extPct = task.extract_total > 0 ? Math.min(100, (task.extract_current / task.extract_total) * 100) : 0;
  const showPct = task.phase === "extract" ? extPct : pct;

  let progressBlock = "";
  if (isQueued) {
    progressBlock = `<div class="dock-meta resolving">${escapeHtml(taskStatusLine(task))}</div>`;
  } else if (isWait && waitSecs > 0) {
    progressBlock = `
      <div class="dock-meta resolving">${escapeHtml(taskStatusLine(task))}</div>
      <div class="dock-countdown">${waitSecs}s</div>
    `;
  } else if (isWait) {
    progressBlock = `<div class="dock-meta resolving">${escapeHtml(taskStatusLine(task))}</div>`;
  } else {
    progressBlock = `
      <div class="progress-bar"><div class="progress-fill ${task.phase === "extract" ? "extract" : ""}" style="width:${showPct}%"></div></div>
      <div class="dock-meta">${showPct.toFixed(1)}% | ${formatBytes(task.downloaded)} / ${formatBytes(task.total_size)} | ${formatSpeed(task.speed)} | ETA ${formatEta(task.downloaded, task.total_size, task.speed)}</div>
    `;
  }

  inner.dataset.dockTaskId = task.task_id;
  inner.dataset.dockTaskState = String(task.state || "");
  inner.dataset.dockTaskPhase = String(task.phase || "");

  inner.innerHTML = `
    <div class="dock-title" title="${escapeAttr(displayText(task.title))}">${text(task.title)}</div>
    <div class="dock-progress-wrap">${progressBlock}</div>
    <div class="dock-actions" data-task-id="${escapeAttr(task.task_id)}">
      ${buildTaskControls(task, { compact: true })}
      <button type="button" class="btn small" id="dockOpenDownloads">${t("downloads.details") || "Details"}</button>
    </div>
  `;
}

function patchDockProgress(inner, task) {
  const wrap = inner.querySelector(".dock-progress-wrap");
  if (!wrap) return;

  const pct = taskProgressPct(task);
  const isQueued = task.phase === "queued";
  const isWait = task.phase === "resolving" || task.phase === "rate_limit";
  const waitSecs = task.rate_limit_seconds || 0;
  const extPct = task.extract_total > 0 ? Math.min(100, (task.extract_current / task.extract_total) * 100) : 0;
  const showPct = task.phase === "extract" ? extPct : pct;

  const fill = wrap.querySelector(".progress-fill");
  const meta = wrap.querySelector(".dock-meta:not(.resolving)") || wrap.querySelector(".dock-meta");
  const countdown = wrap.querySelector(".dock-countdown");

  if (fill) {
    fill.style.width = `${showPct}%`;
    fill.classList.toggle("extract", task.phase === "extract");
  }
  if (meta && !isQueued && !isWait) {
    meta.textContent = `${showPct.toFixed(1)}% | ${formatBytes(task.downloaded)} / ${formatBytes(task.total_size)} | ${formatSpeed(task.speed)} | ETA ${formatEta(task.downloaded, task.total_size, task.speed)}`;
  }
  if (meta && meta.classList.contains("resolving")) {
    meta.textContent = taskStatusLine(task);
  } else if (meta && (isQueued || isWait)) {
    meta.textContent = taskStatusLine(task);
  }
  if (countdown && isWait) {
    countdown.textContent = `${waitSecs}s`;
  }

  const title = inner.querySelector(".dock-title");
  if (title) {
    const label = displayText(task.title);
    title.textContent = label;
    title.title = label;
  }
}

function applyGateProgress(payload) {
  const label = displayText(payload.label || "");
  const resolving = Object.values(state.tasks).filter((task) => task.phase === "resolving");
  Object.values(state.tasks).forEach((task) => {
    if (task.phase !== "resolving") return;
    if (label) {
      const title = displayText(task.title || "");
      if (title !== label && resolving.length !== 1) return;
      if (title !== label && resolving.length === 1 && task !== resolving[0]) return;
    }
    task.verify_countdown = true;
    task.rate_limit_seconds = Math.max(0, Number(payload.countdown) || 0);
    task.status_message = payload.status_message || task.status_message;
  });
}

function mergeVerifyCountdown(prev, next) {
  if (!prev || !next || !prev.verify_countdown || !next.verify_countdown) return next;
  if (prev.phase !== "resolving" || next.phase !== "resolving") return next;
  const prevWait = Number(prev.rate_limit_seconds) || 0;
  const nextWait = Number(next.rate_limit_seconds) || 0;
  if (nextWait > prevWait) {
    next.rate_limit_seconds = prevWait;
    next.status_message = prev.status_message || next.status_message;
  }
  return next;
}

async function loadTasks() {
  try {
    const data = await api("/api/downloads");
    // Backend list is authoritative: rebuild so tasks the server has already
    // finished/removed don't linger in the UI (e.g. stale "Resolving..." rows).
    const fresh = {};
    (data.tasks || []).forEach((task) => {
      if (!isTaskActive(task)) return;
      const prev = state.tasks[task.task_id];
      fresh[task.task_id] = mergeVerifyCountdown(prev, task);
    });
    state.tasks = fresh;
    if (state.tab === "downloads") renderTasks();
    else renderDownloadDock();
  } catch (_) {}
}

function taskControlsKey(task) {
  const isQueued = task.phase === "queued";
  const canControl = !isQueued && ["Queued", "Connecting", "Allocating", "Downloading", "Merging", "Paused"].includes(task.state);
  const isResolving = task.phase === "resolving" || task.phase === "rate_limit";
  const showRetry = canRetryTask(task);
  const showRefresh = canRefreshDownloadLink(task);
  return `${task.phase}|${task.state}|${canControl}|${isResolving}|${showRetry}|${showRefresh}|${task.link_refresh ? 1 : 0}`;
}

function patchTaskCardEl(card, task) {
  const pct = task.total_size > 0 ? Math.min(100, (task.downloaded / task.total_size) * 100) : 0;
  const extPct = task.extract_total > 0 ? Math.min(100, (task.extract_current / task.extract_total) * 100) : 0;
  const statusLine = taskStatusLine(task);

  const mainFill = card.querySelector(".progress-bar .progress-fill:not(.extract)");
  if (mainFill) mainFill.style.width = `${pct}%`;

  const info = card.querySelector(".task-info:not(.resolving)");
  if (info) {
    info.textContent = `${pct.toFixed(1)}% | ${formatBytes(task.downloaded)} / ${formatBytes(task.total_size)} | ${formatSpeed(task.speed)} | ETA ${formatEta(task.downloaded, task.total_size, task.speed)} | ${statusLine}`;
  }

  const resolving = card.querySelector(".task-info.resolving");
  if (resolving) resolving.textContent = statusLine;

  const countdown = card.querySelector(".rate-limit-countdown");
  if (countdown && task.rate_limit_seconds > 0) {
    countdown.textContent = `${task.rate_limit_seconds}s`;
  }

  const extFill = card.querySelector(".progress-fill.extract");
  if (extFill) extFill.style.width = `${extPct}%`;

  const pauseBtn = card.querySelector(".pause-btn");
  if (pauseBtn) {
    pauseBtn.textContent = task.state === "Paused"
      ? (t("download.resume") || "Resume")
      : (t("download.pause") || "Pause");
  }
}

function tryPatchTaskList(active, queued) {
  const list = $("#taskList");
  if (!list) return false;

  const ordered = [...active, ...queued];
  const cards = list.querySelectorAll(".task-card[data-task-id]");
  if (cards.length !== ordered.length) return false;

  for (let i = 0; i < ordered.length; i++) {
    const task = ordered[i];
    const card = cards[i];
    if (!card || card.dataset.taskId !== task.task_id) return false;
    if (card.dataset.controlsKey !== taskControlsKey(task)) return false;
  }

  ordered.forEach((task, i) => patchTaskCardEl(cards[i], task));
  return true;
}

function renderTaskCard(task) {
  const pct = task.total_size > 0 ? Math.min(100, (task.downloaded / task.total_size) * 100) : 0;
  const extPct = task.extract_total > 0 ? Math.min(100, (task.extract_current / task.extract_total) * 100) : 0;
  const card = document.createElement("div");
  card.className = task.phase === "queued" ? "task-card task-card--queued" : "task-card";
  card.dataset.taskId = task.task_id;
  card.dataset.controlsKey = taskControlsKey(task);

  const isResolving = task.phase === "resolving" || task.phase === "rate_limit";
  const isQueued = task.phase === "queued";
  const statusLine = taskStatusLine(task);
  const waitLine = task.rate_limit_seconds > 0
    ? `<div class="rate-limit-countdown">${task.rate_limit_seconds}s</div>`
    : "";
  const showRetry = canRetryTask(task);
  card.innerHTML = `
    <div class="task-title">
      <span>${text(task.title)}</span>
      <div class="task-controls" data-task-id="${escapeAttr(task.task_id)}">
        ${buildTaskControls(task)}
      </div>
    </div>
    ${isQueued ? `
      <div class="task-info resolving">${escapeHtml(statusLine)}</div>
    ` : isResolving ? `
      <div class="task-info resolving">${escapeHtml(statusLine)}</div>
      ${waitLine}
    ` : `
    <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
    <div class="task-info">${pct.toFixed(1)}% | ${formatBytes(task.downloaded)} / ${formatBytes(task.total_size)} | ${formatSpeed(task.speed)} | ETA ${formatEta(task.downloaded, task.total_size, task.speed)} | ${escapeHtml(statusLine)}</div>
    `}
    ${task.phase === "extract" || task.phase === "done" ? `
      <div class="task-phase">Extract: ${task.phase === "done" ? "complete" : "in progress..."}</div>
      <div class="progress-bar"><div class="progress-fill extract" style="width:${extPct}%"></div></div>
      <div class="task-info">${escapeHtml(task.extract_message || "")}</div>
    ` : ""}
    ${task.error && !showRetry ? `<div class="task-error">${escapeHtml(task.error)}</div>` : ""}
    ${task.error && showRetry ? `<div class="task-info resolving">${escapeHtml(task.error)}</div>` : ""}
  `;
  return card;
}

function renderTasks() {
  const list = $("#taskList");
  const tasks = Object.values(state.tasks).filter(isTaskActive);
  const { active, queued } = sortTasksForDisplay(tasks);
  $("#taskEmpty").classList.toggle("hidden", tasks.length > 0);

  if (tryPatchTaskList(active, queued)) {
    renderDownloadDock();
    gpRefresh();
    return;
  }

  list.innerHTML = "";

  active.forEach((task) => {
    list.appendChild(renderTaskCard(task));
  });

  if (queued.length) {
    const header = document.createElement("div");
    header.className = "task-queue-header";
    header.innerHTML = `<h3>${escapeHtml(t("downloads.queueHeading") || "Download Queue")}</h3><span class="task-queue-count">${queued.length}</span>`;
    list.appendChild(header);
    queued.forEach((task) => {
      list.appendChild(renderTaskCard(task));
    });
  }

  renderDownloadDock();
  gpRefresh();
}

// ── Library ──
function showLibraryHealToast(heal) {
  if (!heal) return;
  const imported = heal.auto_imported || 0;
  const merged = heal.merged_legacy || 0;
  if (imported > 0) {
    setStatus(t("library.healImported", { count: imported }));
    return;
  }
  if (heal.restored_backup || (heal.recovered_corrupt && merged > 0)) {
    setStatus(t("library.healRestored", { count: merged || heal.entry_count || 0 }));
    return;
  }
  if (heal.recovered_corrupt) {
    setStatus(t("library.healRestored", { count: heal.entry_count || 0 }));
    return;
  }
  if (heal.manual) {
    setStatus(t("library.healUpToDate"));
  }
}

function renderLibraryWarning(warning) {
  const el = $("#libraryWarning");
  if (!el) return;
  if (warning !== "library_corrupt_unrecoverable") {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML = `<strong>${text(t("library.warnUnrecoverableTitle"))}</strong> ${text(t("library.warnUnrecoverableBody"))}`;
}

async function refreshLibraryState() {
  try {
    const data = await api("/api/library");
    state.library = data.entries || [];
    state.libraryLoadWarning = data.load_warning || "";
    state.libraryPath = data.library_path || "";
    renderLibraryWarning(state.libraryLoadWarning);
    if (data.heal_summary && !state.libraryHealNotified) {
      state.libraryHealNotified = true;
      showLibraryHealToast(data.heal_summary);
    }
  } catch (_) {}
  return state.library;
}

async function loadLibrary() {
  try {
    const data = await api("/api/library");
    state.library = data.entries || [];
    state.libraryLoadWarning = data.load_warning || "";
    state.libraryPath = data.library_path || "";
    renderLibraryWarning(state.libraryLoadWarning);
    renderLibrary();
  } catch (e) {
    setStatus(`Library error: ${e.message}`);
  }
}

async function scanLibraryFolder() {
  try {
    setStatus(t("library.rescanning"));
    const data = await api("/api/library/reconcile", { method: "POST" });
    await loadLibrary();
    showLibraryHealToast({ ...data, manual: true });
  } catch (e) {
    setStatus(t("library.scanFailed", { msg: e.message }));
  }
}

function renderLibrary() {
  const grid = $("#libraryGrid");
  grid.innerHTML = "";
  $("#libraryEmpty").classList.toggle("hidden", state.library.length > 0);

  state.library.forEach((entry) => {
    const wrap = document.createElement("div");
    wrap.className = "library-card";

    const card = createGameCard(
      { title: entry.title, image_url: entry.image_url },
      { showHint: false, onClick: () => launchGame(entry.entry_id) },
    );
    wrap.appendChild(card);

    const exeName = entry.exe_path ? entry.exe_path.split(/[/\\]/).pop() : t("library.noExe");
    const exeLabel = document.createElement("div");
    exeLabel.className = "exe-label";
    exeLabel.textContent = exeName;
    wrap.appendChild(exeLabel);

    const actions = document.createElement("div");
    actions.className = "library-actions";
    actions.innerHTML = `
      <button class="btn accent play-btn">${t("library.play")}</button>
      <button class="btn exe-btn">${t("library.exe")}</button>
      <button class="btn danger del-btn">${t("library.delete")}</button>
    `;
    actions.querySelector(".play-btn").addEventListener("click", (e) => { e.stopPropagation(); launchGame(entry.entry_id); });
    actions.querySelector(".exe-btn").addEventListener("click", (e) => { e.stopPropagation(); openLibraryExePicker(entry.entry_id); });
    actions.querySelector(".del-btn").addEventListener("click", (e) => { e.stopPropagation(); deleteGame(entry.entry_id, entry.title, entry.install_dir); });
    wrap.appendChild(actions);

    grid.appendChild(wrap);
  });
  gpRefresh();
}

async function launchGame(entryId) {
  try {
    const res = await api(`/api/library/${entryId}/launch`, { method: "POST" });
    if (res.needs_picker) {
      if (window.BigPicture?.isOpen?.()) window.BigPicture.close();
      openLibraryExePicker(entryId);
      return;
    }
    setStatus("Launching game...");
  } catch (e) {
    setStatus(`Launch failed: ${e.message}`);
  }
}

function openBigPictureMode() {
  if (!state.library?.length) {
    setStatus(t("bigpicture.empty"));
    return;
  }
  window.GamepadNav?.setEnabled(true);
  const ok = window.BigPicture?.open(state.library, {
    onPlay: (entryId) => launchGame(entryId),
    onClose: () => {
      applyI18n();
      gpRefresh();
    },
  });
  if (ok) applyI18n();
}

async function deleteGame(entryId, title, installDir) {
  const ok = await showConfirm({
    title: t("delete.confirmTitle", { title: displayText(title) }),
    message: t("delete.confirmMsg", { dir: installDir }),
    confirmText: t("confirm.delete"),
    cancelText: t("confirm.cancel"),
    danger: true,
  });
  if (!ok) return;
  try {
    await api(`/api/library/${entryId}`, { method: "DELETE" });
    loadLibrary();
    setStatus(t("status.deleted", { title: displayText(title) }));
  } catch (e) {
    setStatus(t("status.deleteFailed", { msg: e.message }));
    loadLibrary();
  }
}

// ── EXE Picker ──
function showExeModal(title) {
  $("#exeModalTitle").textContent = `${t("exe.title")} — ${displayText(title)}`;
  $("#exeModal").classList.remove("hidden");
  gpRefresh();
}

function hideExeModal() {
  $("#exeModal").classList.add("hidden");
  state.exeModal = { taskId: null, entryId: null, selected: "", mandatory: false, title: "", installDir: "", imageUrl: "" };
  $("#exeModalSub").textContent = t("exe.sub");
  $("#exeRescan").classList.remove("hidden");
  $("#exeLater").classList.add("hidden");
  gpRefresh();
}

async function loadPendingExe() {
  try {
    const data = await api("/api/pending-exe");
    state.pendingExe = data.pending || [];
    renderPendingExe();
    updatePendingBadge();
  } catch (_) {}
}

function updatePendingBadge() {
  const count = state.pendingExe.length;
  const tab = document.querySelector('.tab[data-tab="downloads"]');
  if (!tab) return;
  let badge = tab.querySelector(".tab-badge");
  if (count > 0) {
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "tab-badge";
      tab.appendChild(badge);
    }
    badge.textContent = String(count);
  } else if (badge) {
    badge.remove();
  }
}

function renderPendingExe() {
  const html = state.pendingExe.length
    ? `
    <h3 class="pending-exe-title">Action required — choose PLAY executable</h3>
    ${state.pendingExe.map((p) => `
      <div class="pending-exe-card">
        <span>${text(p.title)}</span>
        <button class="btn accent small" data-pending-task="${escapeAttr(p.task_id)}">Choose EXE</button>
      </div>
    `).join("")}
  `
    : "";

  ["#globalPendingExe", "#pendingExeList"].forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    if (!html) {
      el.classList.add("hidden");
      el.innerHTML = "";
      return;
    }
    el.classList.remove("hidden");
    el.innerHTML = html;
    el.querySelectorAll("[data-pending-task]").forEach((btn) => {
      btn.addEventListener("click", () => openPendingExePicker(btn.dataset.pendingTask, false));
    });
  });
}

function renderExeList(exes, selected) {
  const list = $("#exeList");
  list.innerHTML = "";
  exes.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item.label || item.path;
    li.dataset.path = item.path;
    if (item.path === selected) li.classList.add("selected");
    li.addEventListener("mouseenter", () => window.GamepadNav?.blur?.());
    li.addEventListener("click", () => {
      $$("#exeList li").forEach((l) => l.classList.remove("selected", "gp-focused"));
      li.classList.add("selected");
      state.exeModal.selected = item.path;
    });
    list.appendChild(li);
  });
  if (!state.exeModal.selected && exes.length) {
    state.exeModal.selected = selected || exes[0].path;
    const first = list.querySelector(`[data-path="${CSS.escape(state.exeModal.selected)}"]`);
    if (first) first.classList.add("selected");
  }
  gpRefresh();
}

async function openPendingExePicker(taskId, mandatory = true) {
  try {
    const data = await api(`/api/pending-exe/${encodeURIComponent(taskId)}`);
    state.exeModal = {
      taskId,
      entryId: null,
      selected: data.default_exe,
      mandatory,
      title: data.title,
      installDir: data.install_dir,
      imageUrl: data.image_url || "",
    };
    renderExeList(data.exes, data.default_exe);
    showExeModal(data.title);
    $("#exeModalSub").textContent = mandatory ? t("exe.sub") : t("exe.subOptional");
    $("#exeLater").classList.toggle("hidden", !taskId);
  } catch (_) {}
}

async function openLibraryExePicker(entryId) {
  try {
    const data = await api(`/api/library/${entryId}/exes`);
    state.exeModal = {
      taskId: null,
      entryId,
      selected: data.current_exe || (data.exes[0]?.path || ""),
      mandatory: false,
      title: data.title,
      installDir: data.install_dir,
      imageUrl: data.image_url || "",
    };
    renderExeList(data.exes, state.exeModal.selected);
    showExeModal(data.title);
    $("#exeModalSub").textContent = t("exe.subOptional");
    $("#exeRescan").classList.add("hidden");
  } catch (e) {
    setStatus(`EXE scan failed: ${e.message}`);
  }
}

$("#exeConfirm").addEventListener("click", async () => {
  const { taskId, entryId, selected, mandatory } = state.exeModal;
  if (!selected) return;
  try {
    if (taskId) {
      await api(`/api/pending-exe/${encodeURIComponent(taskId)}/confirm`, {
        method: "POST",
        body: JSON.stringify({ exe_path: selected }),
      });
    } else if (entryId) {
      await api(`/api/library/${entryId}/exe`, {
        method: "PUT",
        body: JSON.stringify({ exe_path: selected }),
      });
    }
    hideExeModal();
    loadPendingExe();
    loadLibrary();
    if (mandatory) switchTab("library");
    setStatus(t("exe.playUpdated"));
  } catch (e) {
    setStatus(`Failed: ${e.message}`);
  }
});

$("#exeLater").addEventListener("click", async () => {
  const { taskId } = state.exeModal;
  if (taskId) {
    try {
      await api(`/api/pending-exe/${encodeURIComponent(taskId)}/skip`, { method: "POST" });
    } catch (_) {}
  }
  hideExeModal();
  loadPendingExe();
  loadLibrary();
  setStatus(t("exe.skippedMsg"));
});

$("#exeRescan").addEventListener("click", async () => {
  const { taskId } = state.exeModal;
  if (!taskId) return;
  const data = await api(`/api/pending-exe/${encodeURIComponent(taskId)}/rescan`, { method: "POST" });
  renderExeList(data.exes, data.default_exe);
  state.exeModal.selected = data.default_exe;
});

$("#exeModal .modal-backdrop").addEventListener("click", () => {
  if (state.exeModal.mandatory) return;
  hideExeModal();
});

// ── Logs ──
async function loadLogs() {
  try {
    const data = await api("/api/logs");
    const view = $("#logView");
    view.innerHTML = data.entries.map((e) => {
      const cls = e.level === "WARN" ? "log-warn" : e.level === "ERROR" ? "log-error" : e.level === "DEBUG" ? "log-debug" : "";
      return `<span class="${cls}">[${e.time}] [${e.level}] ${escapeHtml(e.message)}</span>\n`;
    }).join("");
    view.scrollTop = view.scrollHeight;
  } catch (_) {}
}

$("#clearLogs").addEventListener("click", () => { $("#logView").innerHTML = ""; });

// ── SSE events ──
function connectSSE() {
  const es = new EventSource("/api/events");
  es.onopen = () => {
    // Resync the authoritative task list on (re)connect so nothing lingers.
    loadTasks();
  };
  es.onmessage = (ev) => {
    try {
      const { type, payload } = JSON.parse(ev.data);
      if (type === "task_update") {
        if (payload.cancelled || payload.removed || payload.state === "Cancelled"
            || (payload.status_message || "").toLowerCase() === "cancelled") {
          delete state.tasks[payload.task_id];
        } else {
          state.tasks[payload.task_id] = payload;
        }
        renderDownloadDock();
        if (state.tab === "downloads") scheduleRenderTasks();
        else if (payload.title && payload.state && payload.state !== "Cancelled"
            && payload.phase !== "resolving" && payload.phase !== "rate_limit") {
          setStatus(`${payload.title}: ${payload.state}`);
        }
      }
      if (type === "gate_progress") {
        applyGateProgress(payload);
        renderDownloadDock();
        if (state.tab === "downloads") scheduleRenderTasks();
      }
      if (type === "exe_picker") {
        loadPendingExe().then(() => {
          if (payload.task_id) {
            openPendingExePicker(payload.task_id, false);
          }
        });
        setStatus(`Choose EXE for "${displayText(payload.title)}"`);
      }
      if (type === "license_required") {
        showLicenseModal(
          payload.registration_code,
          payload.message,
          payload.device_fingerprint,
          payload.is_abnormal_hwid
        );
        setStatus(`${displayText(payload.title)}: ${t("license.title")}`);
      }
      if (type === "gate_resolve" && payload.gate_url) {
        const api = window.pywebview?.api;
        const openGate = api?.resolve_gate ?? api?.resolve_anker_gate;
        openGate?.call(api, payload.gate_url)?.catch?.(() => {});
      }
      if (type === "library_healed") {
        if (!payload.silent || payload.auto_imported || payload.recovered_corrupt) {
          showLibraryHealToast(payload);
        }
        refreshLibraryState().then(() => {
          if (state.tab === "library") renderLibrary();
        });
      }
      if (type === "library_updated") {
        refreshLibraryState().then(() => {
          if (state.tab === "library") renderLibrary();
          else if (state.tab === "browse") renderBrowseGrid(state.games);
          if (state.detailGame) updateDetailLibraryState();
        });
      }
      if (type === "settings_updated") {
        state.settings = payload;
        applyLogPanelVisibility();
        if (state.tab === "settings") loadSettingsForm();
        if (state.config) {
          $("#rootHint").textContent = `Games folder: ${payload.download_dir}`;
        }
      }
      if (type === "store_changed") {
        state.settings = payload;
        populateStoreSelect();
        closeGameDetail();
        state.page = 1;
        state.searchQuery = "";
        state.category = "all";
        renderCategoryBar();
        reloadCategories().then(async () => {
          if (state.tab !== "browse") switchTab("browse");
          await loadBrowse();
        });
      }
    } catch (_) {}
  };
  es.onerror = () => {
    es.close();
    setTimeout(connectSSE, 3000);
  };
}

// ── Init ──
async function enableControllerNavigation({ persist = true, notify = true } = {}) {
  window.GamepadNav?.setEnabled(true);
  const cb = $("#controllerEnabledCheck");
  if (cb) cb.checked = true;
  if (!persist || state.settings?.controller_enabled) return;
  try {
    state.settings = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ controller_enabled: true }),
    });
    if (notify) setStatus(t("settings.controllerAuto"));
  } catch (_) {}
}

async function maybeAutoEnableController() {
  try {
    const gp = await api("/api/gamepad/state");
    if (gp.connected) {
      await enableControllerNavigation({
        persist: true,
        notify: !state.settings?.controller_enabled,
      });
    }
  } catch (_) {}
}

async function maybeStartBigPictureOnLaunch() {
  if (!state.settings?.allow_big_picture) return;
  if (!state.library?.length) return;
  switchTab("library");
  window.GamepadNav?.setEnabled(true);
  requestAnimationFrame(() => {
    requestAnimationFrame(() => openBigPictureMode());
  });
}

async function maybeShowStartupActivation() {
  try {
    const data = await api("/api/activation/startup");
    if (!data?.show) return;
    showLicenseModal(
      data.code,
      null,
      data.device_fingerprint || "",
      Boolean(data.custom_os),
      true,
    );
  } catch (_) {}
}

async function init() {
  try {
    const [cfg, cats] = await Promise.all([
      api("/api/config"),
      api("/api/categories"),
    ]);
    state.config = cfg;
    state.categories = cats.categories || [];
    const appTitle = cfg.app_title || `QuickPlay ${cfg.version || ""}`.trim();
    const appBrand = cfg.app_brand || "QuickPlay";
    if (appTitle) document.title = appTitle;
    const brand = $("#appBrand") || document.querySelector(".brand");
    if (brand) brand.textContent = appBrand;
    $("#rootHint").textContent = t("status.gamesFolder", { dir: cfg.download_dir });
    renderCategoryBar();
  } catch (_) {}
  await loadSettings();
  populateStoreSelect();
  i18nSetLang(state.settings?.language || "en");
  applyI18n();
  await maybeShowStartupActivation();
  const gamesDir = state.settings?.download_dir || state.config?.download_dir;
  if (gamesDir) $("#rootHint").textContent = t("status.gamesFolder", { dir: gamesDir });
  await refreshLibraryState();
  const scanBtn = $("#libraryScanBtn");
  if (scanBtn) scanBtn.addEventListener("click", scanLibraryFolder);
  const bpBtn = $("#libraryBigPictureBtn");
  if (bpBtn) bpBtn.addEventListener("click", openBigPictureMode);
  connectSSE();
  wireDownloadTaskActions();
  loadBrowse();
  loadPendingExe();
  wireContactLinks();
  await maybeShowStartupAnnouncement();
  await maybeStartBigPictureOnLaunch();
  window.GamepadNav?.init({
    switchTab,
    closeGameDetail,
    hideExeModal,
    onGamepadActivity: () => enableControllerNavigation({ persist: true, notify: true }),
  });
  if (state.settings?.controller_enabled) {
    window.GamepadNav?.setEnabled(true);
  } else {
    await maybeAutoEnableController();
  }
  setInterval(() => {
    let ticked = false;
    Object.values(state.tasks).forEach((t) => {
      const shouldTick = (t.phase === "rate_limit" && !t.verify_countdown)
        || (t.phase === "resolving" && t.verify_countdown);
      if (!shouldTick || t.rate_limit_seconds <= 0) return;
      t.rate_limit_seconds -= 1;
      if (t.rate_limit_seconds < 0) t.rate_limit_seconds = 0;
      if (t.verify_countdown) {
        if (t.rate_limit_seconds > 0) {
          t.status_message = `Preparing download — ${t.rate_limit_seconds}s`;
          } else if (/^Preparing download — \d+s$/.test(t.status_message || "")) {
            t.status_message = "Security check — please wait";
          } else if (!(t.status_message || "").includes("Security check")) {
            t.status_message = "Security check — please wait";
          }
      }
      ticked = true;
    });
    if (ticked) {
      if (state.tab === "downloads") scheduleRenderTasks();
      else renderDownloadDock();
    }
  }, 1000);

  setInterval(() => {
    loadTasks();
    if (state.tab === "downloads") loadLogs();
  }, 2000);
}

document.addEventListener("DOMContentLoaded", init);
