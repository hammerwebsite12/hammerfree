/**
 * AstralGames (astralgames.net) download URL resolver — Worker-side (Server 3).
 */

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

// Next.js RSC embeds JSON with backslash-escaped quotes.
const DOWNLOAD_LINK_RE =
  /\\?"download_link\\?":\\?"(https?:[^"\\]+)/i;
const DOWNLOAD_OPTION_URL_RE =
  /\\?"url\\?":\\?"(https?:[^"\\]*pearcrypt\.lol[^"\\]*)/i;
const PEAR_CONTAINER_RE =
  /pearcrypt\.lol\/container\/([0-9a-f-]{36})/i;
const MOCHA_SHARE_RE = /mocha\.my\/share\/([A-Za-z0-9_-]+)/i;

function normalizeDownloadUrl(url) {
  return String(url || "")
    .trim()
    .replace(/\\\//g, "/");
}

function extractDownloadUrl(pageHtml) {
  let match = DOWNLOAD_LINK_RE.exec(pageHtml);
  if (match) {
    return normalizeDownloadUrl(match[1]);
  }
  match = DOWNLOAD_OPTION_URL_RE.exec(pageHtml);
  if (match) {
    return normalizeDownloadUrl(match[1]);
  }
  return "";
}

function upstreamError(code, status, extra = {}) {
  const err = new Error(code);
  err.code = code;
  err.upstreamStatus = status;
  Object.assign(err, extra);
  return err;
}

function mirrorLinkScore(url) {
  const u = String(url || "").trim().toLowerCase();
  if (!u.startsWith("http")) return 0;
  if (MOCHA_SHARE_RE.test(u) || u.includes("mocha.my/api/shares/")) return 100;
  if (/\.(7z|zip|rar|tar|gz|001)(\?|#|$)/i.test(u)) return 85;
  if (u.includes("dlproxy")) return 75;
  if (u.includes("zerofs.link")) return 60;
  if (u.includes("fileq.net")) return 55;
  if (u.endsWith(".html")) return 5;
  return 40;
}

function pickBestMirrorLink(links) {
  const alive = (links || []).filter(
    (link) => link?.url && link.is_alive !== false,
  );
  if (!alive.length) {
    throw upstreamError("no_download_url", 404);
  }
  alive.sort(
    (a, b) => mirrorLinkScore(b.url) - mirrorLinkScore(a.url),
  );
  const best = alive[0];
  const score = mirrorLinkScore(best.url);
  if (score < 50) {
    throw upstreamError("unsupported_mirror", 502, {
      mirror_host: String(best.url),
    });
  }
  return String(best.url).trim();
}

function normalizeMirrorFileUrl(fileUrl) {
  let url = String(fileUrl || "").trim();
  const mocha = MOCHA_SHARE_RE.exec(url);
  if (mocha) {
    url = `https://mocha.my/api/shares/${mocha[1]}/download`;
  }
  return url;
}

async function unwrapContainerUrl(containerUrl) {
  const idMatch = PEAR_CONTAINER_RE.exec(containerUrl);
  if (!idMatch) return containerUrl;

  const resp = await fetch(
    `https://pearcrypt.lol/api/container/${idMatch[1]}/mirrors`,
    {
      headers: {
        "User-Agent": USER_AGENT,
        Accept: "application/json",
      },
    },
  );
  if (!resp.ok) {
    throw upstreamError("mirror_list_failed", resp.status);
  }

  let data;
  try {
    data = await resp.json();
  } catch {
    throw upstreamError("mirror_list_failed", 502);
  }

  const links = [];
  for (const mirror of data.mirrors || []) {
    for (const link of mirror.links || []) {
      if (link?.url) links.push(link);
    }
  }

  const fileUrl = normalizeMirrorFileUrl(pickBestMirrorLink(links));
  return fileUrl;
}

export async function resolveAstralDownloadUrl(env, slug) {
  const base = (env.ASTRAL_BASE_URL || "https://astralgames.net").replace(
    /\/$/,
    "",
  );
  const clean = String(slug || "")
    .trim()
    .replace(/^\/+|\/+$/g, "");
  if (!clean) {
    throw upstreamError("bad_game_id", 400);
  }

  const gamePath = `/game/${encodeURIComponent(clean)}`;
  const resp = await fetch(`${base}${gamePath}`, {
    headers: {
      "User-Agent": USER_AGENT,
      Accept: "text/html,application/xhtml+xml",
      "Accept-Language": "en-US,en;q=0.9",
    },
  });

  if (!resp.ok) {
    throw upstreamError("game_page_failed", resp.status);
  }

  const html = await resp.text();
  const containerUrl = extractDownloadUrl(html);
  if (!containerUrl.startsWith("http")) {
    throw upstreamError("no_download_url", 404);
  }

  const downloadUrl = await unwrapContainerUrl(containerUrl);
  if (!downloadUrl.startsWith("http")) {
    throw upstreamError("no_download_url", 404);
  }

  return { download_url: downloadUrl };
}

export function mapAstralUpstreamError(err) {
  const code = err?.code || "upstream_error";
  if (code === "no_download_url" || code === "bad_game_id") {
    return { body: { error: "game_not_downloadable" }, status: 404 };
  }
  if (code === "unsupported_mirror") {
    return { body: { error: "unsupported_mirror" }, status: 502 };
  }
  if (code === "game_page_failed" || code === "mirror_list_failed") {
    return { body: { error: code }, status: 502 };
  }
  return { body: { error: code }, status: 502 };
}
