/**
 * AnkerGames upstream download URL resolver (Worker-side formula).
 * See anker/FORMULA.md in the main repo.
 */

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

const DOWNLOAD_ID_PATTERN = /generateDownloadUrl\(\s*(\d+)\s*\)/i;
const GATE_CDN_PATTERN = /downloadPage\(\s*['"]([^'"]+)['"]/i;

class CookieJar {
  constructor() {
    /** @type {Map<string, string>} */
    this.cookies = new Map();
  }

  /** @param {Response} response */
  absorb(response) {
    const raw =
      typeof response.headers.getSetCookie === "function"
        ? response.headers.getSetCookie()
        : [];
    for (const line of raw) {
      const part = line.split(";")[0];
      const eq = part.indexOf("=");
      if (eq <= 0) continue;
      const name = part.slice(0, eq).trim();
      const value = part.slice(eq + 1).trim();
      if (name) this.cookies.set(name, value);
    }
  }

  header() {
    return [...this.cookies.entries()].map(([k, v]) => `${k}=${v}`).join("; ");
  }
}

function baseHeaders(jar, extra = {}) {
  const headers = {
    "User-Agent": USER_AGENT,
    ...extra,
  };
  const cookie = jar.header();
  if (cookie) headers.Cookie = cookie;
  return headers;
}

/** Laravel JSON often leaves literal \/ in URL strings after parse. */
function normalizeDownloadUrl(url) {
  return String(url || "")
    .trim()
    .replace(/\\\//g, "/");
}

function ankergamesHost(base) {
  try {
    return new URL(base).host.toLowerCase();
  } catch {
    return "ankergames.to";
  }
}

/** Signed /download/… or /download-file/… hops on the Anker host. */
function isAnkerGateUrl(url, baseHost) {
  try {
    const parsed = new URL(url);
    if (parsed.host.toLowerCase() !== baseHost) {
      return false;
    }
    const path = parsed.pathname.toLowerCase();
    return path.startsWith("/download/") || path.startsWith("/download-file/");
  } catch {
    return false;
  }
}

function isArchiveContentType(contentType) {
  const ct = String(contentType || "").toLowerCase();
  return (
    ct.includes("application/zip") ||
    ct.includes("application/x-zip") ||
    ct.includes("application/octet-stream") ||
    ct.includes("application/x-7z") ||
    ct.includes("application/x-rar") ||
    ct.includes("application/vnd.rar")
  );
}

function extractGateTarget(html) {
  const match = GATE_CDN_PATTERN.exec(html);
  if (!match) {
    return "";
  }
  return normalizeDownloadUrl(decodeURIComponent(match[1].trim()));
}

/**
 * Walk Anker gate pages until we reach a CDN / dlproxy URL or a direct file response.
 * Stops with gate_turnstile_required if a Turnstile-gated hop cannot be resolved server-side.
 */
async function followGateChain(downloadUrl, base, jar, baseHost, maxHops = 4) {
  let url = normalizeDownloadUrl(downloadUrl);
  let referer = `${base}/`;

  for (let hop = 0; hop < maxHops; hop++) {
    if (!isAnkerGateUrl(url, baseHost)) {
      return url;
    }

    const gateResp = await fetch(url, {
      headers: baseHeaders(jar, {
        Accept: "text/html,application/xhtml+xml,*/*",
        Referer: referer,
      }),
      redirect: "manual",
    });
    jar.absorb(gateResp);

    if (gateResp.status >= 300 && gateResp.status < 400) {
      const location = gateResp.headers.get("Location") || "";
      if (location) {
        url = normalizeDownloadUrl(new URL(location, url).href);
        referer = url;
        continue;
      }
    }

    if (!gateResp.ok) {
      throw upstreamError("gate_page_failed", gateResp.status);
    }

    const contentType = gateResp.headers.get("Content-Type") || "";
    if (isArchiveContentType(contentType)) {
      return url;
    }

    const gateHtml = await gateResp.text();
    const next = extractGateTarget(gateHtml);
    if (!next) {
      throw upstreamError("gate_turnstile_required", 502);
    }

    referer = url;
    url = next;
  }

  if (isAnkerGateUrl(url, baseHost)) {
    throw upstreamError("gate_turnstile_required", 502);
  }
  return url;
}

/**
 * Resolve Anker slug → CDN download URL.
 * @param {Record<string, string>} env
 * @param {string} slug
 * @returns {Promise<{ download_url: string, gate_url?: string }>}
 */
function shouldPreferClientResolve(err) {
  const code = err?.code || "";
  return (
    code === "csrf_failed" ||
    code === "csrf_empty" ||
    code === "csrf_bad_json" ||
    code === "csrf_expired" ||
    code === "gate_turnstile_required" ||
    code === "game_page_failed"
  );
}

export async function resolveAnkerDownloadUrl(env, slug) {
  const base = (env.ANKER_BASE_URL || "https://ankergames.to").replace(/\/$/, "");
  const recaptcha = env.ANKER_RECAPTCHA_BYPASS || "development-mode";
  const jar = new CookieJar();

  let csrfToken = await fetchCsrfToken(base, jar);

  const gamePath = `/game/${encodeURIComponent(slug)}`;
  const pageResp = await fetch(`${base}${gamePath}`, {
    headers: baseHeaders(jar, { Accept: "text/html,application/xhtml+xml" }),
  });
  jar.absorb(pageResp);
  if (!pageResp.ok) {
    throw upstreamError("game_page_failed", pageResp.status);
  }

  const pageHtml = await pageResp.text();
  // Keep the /csrf-token value for POST — the HTML meta token is tied to a
  // different session rotation and causes 419 CSRF mismatch if used here.

  const idMatch = DOWNLOAD_ID_PATTERN.exec(pageHtml);
  if (!idMatch) {
    throw upstreamError("no_download_id", 404);
  }
  const downloadId = idMatch[1];

  let genPayload = await postGenerateDownloadUrl(
    base,
    jar,
    csrfToken,
    downloadId,
    gamePath,
    recaptcha,
  );

  if (genPayload === "csrf_expired") {
    csrfToken = await fetchCsrfToken(base, jar);
    genPayload = await postGenerateDownloadUrl(
      base,
      jar,
      csrfToken,
      downloadId,
      gamePath,
      recaptcha,
    );
    if (genPayload === "csrf_expired") {
      throw upstreamError("csrf_expired", 419);
    }
  }

  let downloadUrl = normalizeDownloadUrl(genPayload.download_url);
  if (!downloadUrl.startsWith("http")) {
    const message = String(genPayload.message || "invalid_download_url");
    throw upstreamError("invalid_download_url", 502, { message });
  }

  const gateUrl = downloadUrl;
  const baseHost = ankergamesHost(base);
  try {
    downloadUrl = await followGateChain(downloadUrl, base, jar, baseHost);
  } catch (err) {
    if (err?.code === "gate_turnstile_required") {
      return { client_resolve: true, gate_url: gateUrl };
    }
    throw err;
  }

  return { download_url: normalizeDownloadUrl(downloadUrl), gate_url: gateUrl };
}

export async function resolveAnkerDownloadUrlSafe(env, slug) {
  try {
    return await resolveAnkerDownloadUrl(env, slug);
  } catch (err) {
    if (shouldPreferClientResolve(err)) {
      return { client_resolve: true };
    }
    throw err;
  }
}

async function fetchCsrfToken(base, jar) {
  const csrfResp = await fetch(`${base}/csrf-token`, {
    headers: baseHeaders(jar, {
      Accept: "application/json",
      "X-Requested-With": "XMLHttpRequest",
    }),
  });
  jar.absorb(csrfResp);
  if (!csrfResp.ok) {
    throw upstreamError("csrf_failed", csrfResp.status);
  }
  try {
    const payload = await csrfResp.json();
    const token = String(payload.token || "").trim();
    if (!token) {
      throw upstreamError("csrf_empty", csrfResp.status);
    }
    return token;
  } catch (err) {
    if (err?.code) throw err;
    throw upstreamError("csrf_bad_json", csrfResp.status);
  }
}

/**
 * @returns {Promise<object|string>} JSON payload, or "csrf_expired" for retry
 */
async function postGenerateDownloadUrl(
  base,
  jar,
  csrfToken,
  downloadId,
  gamePath,
  recaptcha,
) {
  const genResp = await fetch(`${base}/generate-download-url/${downloadId}`, {
    method: "POST",
    headers: baseHeaders(jar, {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
      "X-CSRF-TOKEN": csrfToken,
      Referer: `${base}${gamePath}`,
      Origin: base,
    }),
    body: JSON.stringify({ "g-recaptcha-response": recaptcha }),
  });
  jar.absorb(genResp);

  if (genResp.status === 419) {
    return "csrf_expired";
  }
  if (genResp.status === 429) {
    const retry = parseInt(genResp.headers.get("Retry-After") || "60", 10);
    throw upstreamError("rate_limited", 429, { wait: retry });
  }
  if (!genResp.ok) {
    throw upstreamError("generate_failed", genResp.status);
  }
  try {
    return await genResp.json();
  } catch {
    throw upstreamError("generate_bad_json", genResp.status);
  }
}

function upstreamError(code, status, extra = {}) {
  const err = new Error(code);
  err.code = code;
  err.upstreamStatus = status;
  Object.assign(err, extra);
  return err;
}

export function mapUpstreamError(err) {
  const code = err?.code || "upstream_error";
  if (code === "rate_limited") {
    return { body: { error: code, wait: err.wait || 60 }, status: 429 };
  }
  if (code === "no_download_id") {
    return { body: { error: "game_not_downloadable" }, status: 404 };
  }
  if (code === "csrf_expired") {
    return { body: { error: "csrf_expired" }, status: 502 };
  }
  if (code === "gate_turnstile_required") {
    return {
      body: {
        error: "gate_turnstile_required",
        message: "Anker gate requires browser verification.",
      },
      status: 502,
    };
  }
  return { body: { error: code }, status: 502 };
}
