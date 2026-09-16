/**
 * QuickPlay download-link SIGNER + license gate.
 *
 * playzip blocks datacenter/Cloudflare IPs, so the Worker cannot call the
 * upstream download API directly. Instead it holds the secret and only signs:
 * the client sends a signed request, the Worker checks the HWID userbase, then
 * returns a time-bound digest. The client (residential IP) performs the actual
 * upstream call.
 *
 *   POST /license/check  { hwid, device_fp, ts, nonce, sig, token }
 *     -> { licensed: bool, custom_os?: bool }   (one-time startup probe; no signing)
 *
 *   POST /sign  { game_id, hwid, device_fp, ts, nonce, sig, token, hw_snapshot? }
 *     -> { timestamp, digest, cookie, snapshot_recorded?: true }
 *     -> 403 { error: "license_required", custom_os?: bool }
 *
 * On first licensed /sign, optional hw_snapshot is appended once to the .user
 * file in quickplayusr (requires PAT with repo write). Discord alert optional.
 *
 * Secrets (via `wrangler secret put` / `secret bulk`):
 *   APP_TOKEN, SIGNING_SECRET, PLAYZIP_SECRET_KEY, PLAYZIP_AUTH_COOKIE,
 *   QUICKPLAY_HWID_PAT (read + write quickplayusr), DISCORD_WEBHOOK_URL
 * Vars: SIGNATURE_WINDOW_SECONDS, RATE_LIMIT_PER_MINUTE,
 *       QUICKPLAY_HWID_OWNER, QUICKPLAY_HWID_REPO
 * Binding: PZ_KV (nonce replay dedupe + per-IP rate limiting)
 */

import { auditRequest } from "./audit_log.js";
import {
  abuseReasonFromResponse,
  queueAbuseDiscordAlert,
  trackHourlyVolumeAlert,
} from "./abuse_alert.js";
import { checkIpDenylist, recordRateLimitStrike } from "./ip_denylist.js";
import {
  kvAbuseGuardForRoute,
  kvAuditEnabled,
  kvRateLimitWritesEnabled,
  kvUsageStatsEnabled,
} from "./kv_policy.js";
import {
  fetchHwidUserFile,
  hasHardwareSnapshot,
  isAbnormalHwidStem,
  isLicensedUserFile,
  normalizeDeviceFp,
  normalizeHwidStem,
  sanitizeSnapshot,
  writeHardwareSnapshot,
} from "./hwid_snapshot.js";

const encoder = new TextEncoder();

/** Daily sign-request counts that trigger a one-time Discord alert (UTC day). */
const USAGE_ALERT_THRESHOLDS = [100, 300, 400, 450, 500];

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function hex(buffer) {
  const bytes = new Uint8Array(buffer);
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(text));
  return hex(digest);
}

async function hmacHex(secret, message) {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(message));
  return hex(sig);
}

/** Constant-time string comparison. */
function safeEqual(a, b) {
  const ab = encoder.encode(a);
  const bb = encoder.encode(b);
  if (ab.byteLength !== bb.byteLength) return false;
  return crypto.subtle.timingSafeEqual(ab, bb);
}

function clientIp(request) {
  return (
    request.headers.get("cf-connecting-ip") ||
    request.headers.get("x-forwarded-for") ||
    "unknown"
  );
}

function normalizeHwid(value) {
  const cleaned = String(value || "")
    .trim()
    .replace(/-/g, "")
    .toLowerCase();
  if (!/^[0-9a-f]{32}$/.test(cleaned)) {
    return null;
  }
  return cleaned;
}

/** Verify token + timestamp window + HMAC for /license/check (no game_id). */
async function verifyLicenseCheckRequest(env, body) {
  const { hwid, device_fp, ts, nonce, sig, token } = body || {};

  if (!hwid || !device_fp || !ts || !nonce || !sig || !token) {
    return json({ error: "bad_request" }, 400);
  }
  if (!normalizeHwid(hwid)) {
    return json({ error: "bad_hwid" }, 400);
  }
  if (!normalizeDeviceFp(device_fp)) {
    return json({ error: "bad_device_fp" }, 400);
  }
  if (!safeEqual(String(token), env.APP_TOKEN)) {
    return json({ error: "unauthorized" }, 401);
  }

  const now = Math.floor(Date.now() / 1000);
  const window = parseInt(env.SIGNATURE_WINDOW_SECONDS || "120", 10);
  const drift = Math.abs(now - parseInt(String(ts), 10));
  if (!Number.isFinite(drift) || drift > window) {
    return json({ error: "expired" }, 401);
  }

  const expected = await hmacHex(
    env.SIGNING_SECRET,
    `${ts}.${nonce}.${normalizeHwid(hwid)}.${normalizeDeviceFp(device_fp)}`,
  );
  if (!safeEqual(String(sig), expected)) {
    return json({ error: "bad_signature" }, 401);
  }
  return null;
}

/** Shared HWID license lookup used by /sign and /license/check. */
async function resolveLicenseStatus(env, hwid, deviceFp) {
  const stem = normalizeHwidStem(hwid);
  const abnormal = stem ? isAbnormalHwidStem(stem) : false;

  let userFile;
  try {
    userFile = stem ? await fetchHwidUserFile(env, stem) : null;
  } catch (err) {
    const message = err instanceof Error ? err.message : "license_check_failed";
    if (message === "license_check_unconfigured") {
      return { error: json({ error: "service_unavailable" }, 503) };
    }
    return { error: json({ error: "license_check_failed" }, 502) };
  }

  const licensed = userFile
    ? isLicensedUserFile(userFile.content, stem, deviceFp)
    : false;

  return { licensed, custom_os: abnormal, userFile, stem };
}

/** Verify token + timestamp window + HMAC signature. Returns null if OK. */
async function verifyRequest(env, body) {
  const { game_id, hwid, device_fp, ts, nonce, sig, token } = body || {};

  if (!game_id || !hwid || !device_fp || !ts || !nonce || !sig || !token) {
    return json({ error: "bad_request" }, 400);
  }
  if (!normalizeHwid(hwid)) {
    return json({ error: "bad_hwid" }, 400);
  }
  if (!normalizeDeviceFp(device_fp)) {
    return json({ error: "bad_device_fp" }, 400);
  }
  if (!safeEqual(String(token), env.APP_TOKEN)) {
    return json({ error: "unauthorized" }, 401);
  }

  const now = Math.floor(Date.now() / 1000);
  const window = parseInt(env.SIGNATURE_WINDOW_SECONDS || "120", 10);
  const drift = Math.abs(now - parseInt(String(ts), 10));
  if (!Number.isFinite(drift) || drift > window) {
    return json({ error: "expired" }, 401);
  }

  const expected = await hmacHex(
    env.SIGNING_SECRET,
    `${ts}.${nonce}.${game_id}.${normalizeHwid(hwid)}.${normalizeDeviceFp(device_fp)}`,
  );
  if (!safeEqual(String(sig), expected)) {
    return json({ error: "bad_signature" }, 401);
  }
  return null;
}

function alertOnAbuse(ctx, env, request, body, route, abuse, extra = {}) {
  const reason = abuseReasonFromResponse(abuse);
  if (!reason) return;
  queueAbuseDiscordAlert(ctx, env, request, {
    worker: "dl-resolver",
    route,
    reason,
    hwid: body?.hwid,
    device_fp: body?.device_fp,
    game_id: body?.game_id,
    ...extra,
  });
}

function recordAudit(ctx, env, request, meta) {
  if (!kvAuditEnabled(env)) return;
  auditRequest(ctx, env, meta);
  trackHourlyVolumeAlert(ctx, env, request, {
    ...meta,
    worker: "dl-resolver",
  });
}

/** KV-backed replay (nonce) + per-IP rate limit + denylist. Returns null if OK. */
async function checkAbuse(env, ip, nonce, ctx, request, body, route) {
  if (!env.PZ_KV || !kvAbuseGuardForRoute(env, route)) return null;

  try {
    if (kvRateLimitWritesEnabled(env)) {
      const denied = await checkIpDenylist(env.PZ_KV, ip);
      if (denied) {
        if (ctx && request) {
          queueAbuseDiscordAlert(ctx, env, request, {
            worker: "dl-resolver",
            route: route || "?",
            reason: "ip_denied",
            hwid: body?.hwid,
            device_fp: body?.device_fp,
            game_id: body?.game_id,
            ip,
          });
        }
        return denied;
      }
    }

    const nonceKey = `nonce:${nonce}`;
    if (await env.PZ_KV.get(nonceKey)) {
      return json({ error: "replay" }, 409);
    }

    if (kvRateLimitWritesEnabled(env)) {
      const limit = parseInt(env.RATE_LIMIT_PER_MINUTE || "20", 10);
      const bucket = Math.floor(Date.now() / 60000);
      const rlKey = `rl:${ip}:${bucket}`;
      const current = parseInt((await env.PZ_KV.get(rlKey)) || "0", 10);
      if (current >= limit) {
        const banned = await recordRateLimitStrike(env.PZ_KV, ip);
        if (banned && ctx && request) {
          queueAbuseDiscordAlert(ctx, env, request, {
            worker: "dl-resolver",
            route: route || "?",
            reason: "ip_denylisted",
            hwid: body?.hwid,
            device_fp: body?.device_fp,
            game_id: body?.game_id,
            ip,
          });
        }
        return json({ error: "rate_limited", wait: 60 }, 429);
      }

      await Promise.all([
        env.PZ_KV.put(nonceKey, "1", { expirationTtl: 300 }),
        env.PZ_KV.put(rlKey, String(current + 1), { expirationTtl: 120 }),
      ]);
      return null;
    }

    await env.PZ_KV.put(nonceKey, "1", { expirationTtl: 300 });
    return null;
  } catch {
    // KV quota/outage must not block signing — degrade without rate-limit tracking.
    return null;
  }
}

function utcDayKey(date = new Date()) {
  return date.toISOString().slice(0, 10);
}

async function incrementDailySignCount(env) {
  if (!env.PZ_KV) return 0;
  const day = utcDayKey();
  const key = `usage:sign:${day}`;
  const current = parseInt((await env.PZ_KV.get(key)) || "0", 10);
  const next = current + 1;
  await env.PZ_KV.put(key, String(next), { expirationTtl: 172800 });
  return next;
}

async function sendDiscordUsageAlert(webhookUrl, count, threshold, day) {
  const level =
    threshold >= 500 ? "🔴 CRITICAL" : threshold >= 400 ? "🟠 WARNING" : "🟡 INFO";
  const kvEstimate = count * 2;
  const content =
    `**QuickPlay dl-resolver — Daily Usage ${level}**\n` +
    `**Date (UTC):** \`${day}\`\n` +
    `**Download sign requests today:** \`${count}\`\n` +
    `**Threshold reached:** \`${threshold}\`\n` +
    `**Est. KV writes:** \`~${kvEstimate}\` / 1,000 free/day\n` +
    `**Worker requests limit:** 100,000 free/day\n\n` +
    (threshold >= 500
      ? "⚠️ Malapit na sa KV write limit (~500 downloads/day). I-upgrade ang Workers Paid ($5/mo) kung maraming user."
      : threshold >= 400
        ? "⚠️ Papalapit na sa safe free-tier cap (~500 downloads/day)."
        : "📊 Usage update — subaybayan ang Cloudflare Metrics kung tumataas pa.");

  await fetch(webhookUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

async function maybeSendUsageAlert(env, count) {
  const webhookUrl = env.DISCORD_WEBHOOK_URL;
  if (!webhookUrl || !env.PZ_KV) return;

  if (!USAGE_ALERT_THRESHOLDS.includes(count)) return;

  const day = utcDayKey();
  const sentKey = `usage:alert:${day}:${count}`;
  if (await env.PZ_KV.get(sentKey)) return;

  await env.PZ_KV.put(sentKey, "1", { expirationTtl: 172800 });
  try {
    await sendDiscordUsageAlert(webhookUrl, count, count, day);
  } catch {
    // Non-fatal — do not block downloads if Discord is down.
  }
}

async function trackDailyUsage(env, ctx) {
  if (!kvUsageStatsEnabled(env) || !env.PZ_KV) return;
  ctx.waitUntil(
    incrementDailySignCount(env)
      .then((count) => maybeSendUsageAlert(env, count))
      .catch(() => {}),
  );
}

async function maybeRecordHardwareSnapshot(env, ctx, stem, deviceFp, hwSnapshot, abnormal) {
  if (!stem || !hwSnapshot) return false;
  if (sanitizeSnapshot(hwSnapshot) === null) return false;

  const file = await fetchHwidUserFile(env, stem);
  if (!file || hasHardwareSnapshot(file.content)) return false;
  if (!isLicensedUserFile(file.content, stem, deviceFp)) return false;

  if (abnormal) {
    const snapFp = normalizeDeviceFp(
      String(hwSnapshot).match(/Device-FP:\s*([0-9a-f]{64})/i)?.[1] || "",
    );
    if (!snapFp || snapFp !== deviceFp) return false;
  }

  const lockKey = `snap:lock:${stem}`;
  if (env.PZ_KV && (await env.PZ_KV.get(lockKey))) return false;
  if (env.PZ_KV) {
    await env.PZ_KV.put(lockKey, "1", { expirationTtl: 120 });
  }

  const ok = await writeHardwareSnapshot(env, stem, hwSnapshot, abnormal);
  if (env.PZ_KV && ok) {
    await env.PZ_KV.put(`snap:done:${stem}`, "1");
  }
  return ok;
}

export default {
  async fetch(request, env, ctx) {
    try {
      return await handleRequest(request, env, ctx);
    } catch {
      return json({ error: "internal_error" }, 500);
    }
  },
};

async function handleRequest(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/") {
      return new Response("ok", { status: 200 });
    }

    if (request.method === "POST" && url.pathname === "/license/check") {
      let body;
      try {
        body = await request.json();
      } catch {
        return json({ error: "bad_json" }, 400);
      }

      const bad = await verifyLicenseCheckRequest(env, body);
      if (bad) return bad;

      const ip = clientIp(request);
      const abuse = await checkAbuse(
        env,
        ip,
        body.nonce,
        ctx,
        request,
        body,
        "POST /license/check",
      );
      if (abuse) {
        alertOnAbuse(ctx, env, request, body, "POST /license/check", abuse);
        return abuse;
      }

      const hwid = normalizeHwid(body.hwid);
      const deviceFp = normalizeDeviceFp(body.device_fp);
      const status = await resolveLicenseStatus(env, hwid, deviceFp);
      if (status.error) return status.error;

      recordAudit(ctx, env, request, {
        ip,
        hwid,
        route: "license_check",
        licensed: status.licensed,
      });

      return json({
        licensed: status.licensed,
        custom_os: status.custom_os,
      });
    }

    if (request.method !== "POST" || url.pathname !== "/sign") {
      return json({ error: "not_found" }, 404);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "bad_json" }, 400);
    }

    const bad = await verifyRequest(env, body);
    if (bad) return bad;

    const ip = clientIp(request);
    const abuse = await checkAbuse(
      env,
      ip,
      body.nonce,
      ctx,
      request,
      body,
      "POST /sign",
    );
    if (abuse) {
      alertOnAbuse(ctx, env, request, body, "POST /sign", abuse);
      return abuse;
    }

    trackDailyUsage(env, ctx);

    const hwid = normalizeHwid(body.hwid);
    const deviceFp = normalizeDeviceFp(body.device_fp);
    const status = await resolveLicenseStatus(env, hwid, deviceFp);
    if (status.error) return status.error;

    recordAudit(ctx, env, request, {
      ip,
      hwid,
      route: "sign",
      gameId: body.game_id,
      licensed: status.licensed,
    });

    if (!status.licensed) {
      return json(
        {
          error: "license_required",
          custom_os: status.custom_os,
        },
        403,
      );
    }

    const { userFile, stem } = status;
    const abnormal = status.custom_os;

    const needsSnapshot =
      userFile &&
      !hasHardwareSnapshot(userFile.content) &&
      sanitizeSnapshot(body.hw_snapshot);

    let snapshotRecorded = false;
    if (needsSnapshot) {
      const recordPromise = maybeRecordHardwareSnapshot(
        env,
        ctx,
        stem,
        deviceFp,
        body.hw_snapshot,
        abnormal,
      )
        .then((ok) => ok)
        .catch(() => false);

      ctx.waitUntil(recordPromise);
      snapshotRecorded = await recordPromise;
    }

    const gameId = String(body.game_id);
    const timestamp = String(Math.floor(Date.now() / 1000));
    const digest = await sha256Hex(`${timestamp}${gameId}${env.PLAYZIP_SECRET_KEY}`);

    const responseBody = {
      timestamp,
      digest,
      cookie: `site_auth=1; key=${env.PLAYZIP_AUTH_COOKIE}`,
    };
    if (snapshotRecorded) {
      responseBody.snapshot_recorded = true;
    }

    return json(responseBody);
}
