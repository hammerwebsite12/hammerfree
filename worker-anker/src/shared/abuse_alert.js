/**
 * Discord alerts for abusive resolver traffic (rate limit, replay, high volume).
 */

import { normalizeHwidStem } from "./hwid_snapshot.js";
import { kvAuditEnabled, kvLiteMode } from "./kv_policy.js";

const HOURLY_VOLUME_THRESHOLDS = [50, 80, 100, 150];
const ALERT_DEDUPE_TTL = 48 * 3600;
const HOURLY_COUNTER_TTL = 3 * 3600;

function hourBucket(date = new Date()) {
  return date.toISOString().slice(0, 13).replace("T", "-");
}

function reasonLabel(reason) {
  switch (reason) {
    case "rate_limited":
      return "Rate limited (≥20 req/min on this IP)";
    case "replay":
      return "Replay attack (duplicate nonce)";
    case "high_volume":
      return "High hourly volume (suspicious scraping)";
    case "ip_denied":
      return "IP temporarily blocked (repeat rate-limit abuse)";
    case "ip_denylisted":
      return "IP auto-banned for 1 hour (3+ rate-limit strikes this hour)";
    default:
      return String(reason || "abuse");
  }
}

/** @param {Request} request */
export function geoFromRequest(request) {
  const cf = request.cf || {};
  return {
    country: cf.country || request.headers.get("cf-ipcountry") || "?",
    city: cf.city || "?",
    region: cf.region || cf.regionCode || "?",
    colo: cf.colo || "?",
    timezone: cf.timezone || "?",
    asn: cf.asn != null ? String(cf.asn) : "?",
    org: cf.asOrganization || "?",
  };
}

function clientIp(request) {
  return (
    request.headers.get("cf-connecting-ip") ||
    request.headers.get("x-forwarded-for") ||
    "unknown"
  );
}

function formatAlertBody(details, geo) {
  const stem =
    details.hwidStem ||
    (details.hwid ? normalizeHwidStem(details.hwid) : null) ||
    "?";
  const userFile = stem !== "?" ? `${stem}.user` : "?";
  const fp = String(details.device_fp || "?");
  const fpShort = fp.length > 20 ? `${fp.slice(0, 20)}…` : fp;
  const ip = String(details.ip || "?");

  let text =
    `🚨 **QuickPlay Abuse Alert (Server 2)**\n` +
    `**Worker:** \`${details.worker || "anker-dlresolver"}\`\n` +
    `**Route:** \`${details.route || "?"}\`\n` +
    `**Reason:** ${reasonLabel(details.reason)}\n` +
    `\n**Network**\n` +
    `• IP: \`${ip}\`\n` +
    `• Location: ${geo.city}, ${geo.region}, **${geo.country}**\n` +
    `• Colo: \`${geo.colo}\` | TZ: \`${geo.timezone}\`\n` +
    `• ASN: AS${geo.asn} — ${geo.org}\n` +
    `\n**Ban target**\n` +
    `• HWID stem: \`${stem}\`\n` +
    `• User file: \`hammerwebsite12/quickplayusr/${userFile}\`\n` +
    `• Device-FP: \`${fpShort}\`\n`;

  if (details.game_id) {
    text += `• Game: \`${details.game_id}\`\n`;
  }
  if (details.licensed !== undefined) {
    text += `• Licensed: **${details.licensed ? "yes" : "NO"}**\n`;
  }
  if (details.hourly_count) {
    text += `• Hourly requests (this IP): **${details.hourly_count}**\n`;
  }

  text +=
    `\n**Ban:** delete/rename \`${userFile}\` in quickplayusr (GitHub).\n` +
    `**UTC:** \`${new Date().toISOString()}\``;

  return text;
}

async function sendDiscord(webhookUrl, content) {
  await fetch(webhookUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: content.slice(0, 1900) }),
  });
}

/**
 * @param {ExecutionContext} ctx
 * @param {{ DISCORD_WEBHOOK_URL?: string, ANKER_KV?: KVNamespace }} env
 */
export function queueAbuseDiscordAlert(ctx, env, request, details) {
  const webhookUrl = env.DISCORD_WEBHOOK_URL;
  const kv = env.ANKER_KV;
  if (!webhookUrl || !kv || !ctx) return;
  if (kvLiteMode(env)) return;

  const ip = String(details.ip || clientIp(request));
  const stem =
    details.hwidStem ||
    (details.hwid ? normalizeHwidStem(details.hwid) : null) ||
    "unknown";
  const hour = hourBucket();
  const dedupeKey = `anker:alert:${details.reason}:${hour}:${ip}:${stem}`;

  const geo = geoFromRequest(request);
  const payload = { ...details, ip, hwidStem: stem };

  ctx.waitUntil(
    (async () => {
      if (await kv.get(dedupeKey)) return;
      await kv.put(dedupeKey, "1", { expirationTtl: ALERT_DEDUPE_TTL });
      await sendDiscord(webhookUrl, formatAlertBody(payload, geo));
    })().catch(() => {}),
  );
}

export function trackHourlyVolumeAlert(ctx, env, request, meta) {
  const kv = env.ANKER_KV;
  if (!kvAuditEnabled(env) || !kv || !ctx) return;

  const ip = String(meta.ip || clientIp(request));
  const hour = hourBucket();
  const counterKey = `anker:audit:ip:hour:${hour}:${ip}`;

  ctx.waitUntil(
    (async () => {
      const cur = parseInt((await kv.get(counterKey)) || "0", 10);
      const next = cur + 1;
      await kv.put(counterKey, String(next), { expirationTtl: HOURLY_COUNTER_TTL });

      if (!HOURLY_VOLUME_THRESHOLDS.includes(next)) return;

      const sentKey = `anker:alert:high_volume:${hour}:${ip}:${next}`;
      if (await kv.get(sentKey)) return;
      await kv.put(sentKey, "1", { expirationTtl: ALERT_DEDUPE_TTL });

      const webhookUrl = env.DISCORD_WEBHOOK_URL;
      if (!webhookUrl) return;

      const geo = geoFromRequest(request);
      const body = formatAlertBody(
        {
          worker: meta.worker || "anker-dlresolver",
          route: meta.route,
          reason: "high_volume",
          ip,
          hwid: meta.hwid,
          licensed: meta.licensed,
          game_id: meta.gameId,
          hourly_count: next,
        },
        geo,
      );
      await sendDiscord(webhookUrl, body);
    })().catch(() => {}),
  );
}

export function abuseReasonFromResponse(response) {
  if (!response) return null;
  if (response.status === 429) return "rate_limited";
  if (response.status === 409) return "replay";
  return null;
}
