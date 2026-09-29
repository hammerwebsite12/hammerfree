/**
 * AstralGames (astralgames.net) download URL resolver — Worker-side.
 *
 * Unlike Anker (Laravel CSRF + gate), Astral is a Next.js site that embeds
 * direct download URLs in the game page RSC payload (e.g. pearcrypt.lol containers).
 */

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

const DOWNLOAD_LINK_RE =
  /"download_link":"(https?:\\\/\\\/[^"]+)"/i;
const DOWNLOAD_OPTION_URL_RE =
  /"url":"(https?:\\\/\\\/pearcrypt\.lol[^"]+)"/i;

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

/**
 * Resolve Astral slug → file download URL (often pearcrypt.lol container).
 * @param {Record<string, string>} env
 * @param {string} slug
 * @returns {Promise<{ download_url: string }>}
 */
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
  const downloadUrl = extractDownloadUrl(html);
  if (!downloadUrl.startsWith("http")) {
    throw upstreamError("no_download_url", 404);
  }

  return { download_url: downloadUrl };
}

export function mapUpstreamError(err) {
  const code = err?.code || "upstream_error";
  if (code === "no_download_url" || code === "bad_game_id") {
    return { body: { error: "game_not_downloadable" }, status: 404 };
  }
  if (code === "game_page_failed") {
    return { body: { error: code }, status: 502 };
  }
  return { body: { error: code }, status: 502 };
}
