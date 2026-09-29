/**
 * Shared auth + license gate for Anker Workers (same model as playzip dl-resolver).
 */

import { checkIpDenylist, recordRateLimitStrike } from "./ip_denylist.js";
import { queueAbuseDiscordAlert } from "./abuse_alert.js";
import {
  kvAbuseGuardForRoute,
  kvRateLimitWritesEnabled,
} from "./kv_policy.js";
import { isManualApprovedContent } from "./abnormal_hwids.js";
import {
  fetchHwidUserFile,
  hasHardwareSnapshot,
  isLicensedUserFile,
  normalizeDeviceFp,
  normalizeHwidStem,
  sanitizeSnapshot,
  writeHardwareSnapshot,
  isAbnormalHwidStem,
} from "./hwid_snapshot.js";

const encoder = new TextEncoder();

export function json(body, status = 200) {
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

function safeEqual(a, b) {
  const ab = encoder.encode(a);
  const bb = encoder.encode(b);
  if (ab.byteLength !== bb.byteLength) return false;
  return crypto.subtle.timingSafeEqual(ab, bb);
}

export function clientIp(request) {
  return (
    request.headers.get("cf-connecting-ip") ||
    request.headers.get("x-forwarded-for") ||
    "unknown"
  );
}

export { normalizeDeviceFp };

export function normalizeHwid(value) {
  const cleaned = String(value || "")
    .trim()
    .replace(/-/g, "")
    .toLowerCase();
  if (!/^[0-9a-f]{32}$/.test(cleaned)) {
    return null;
  }
  return cleaned;
}

export async function verifyLicenseCheckRequest(env, body) {
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

/** Verify signed resolve request. game_id = Anker slug. */
export async function verifyResolveRequest(env, body) {
  const { game_id, hwid, device_fp, ts, nonce, sig, token } = body || {};

  if (!game_id || !hwid || !device_fp || !ts || !nonce || !sig || !token) {
    return json({ error: "bad_request" }, 400);
  }
  const slug = String(game_id).trim();
  if (!/^[a-z0-9][a-z0-9-]{0,120}$/i.test(slug)) {
    return json({ error: "bad_game_id" }, 400);
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
    `${ts}.${nonce}.${slug}.${normalizeHwid(hwid)}.${normalizeDeviceFp(device_fp)}`,
  );
  if (!safeEqual(String(sig), expected)) {
    return json({ error: "bad_signature" }, 401);
  }
  return null;
}

export async function resolveLicenseStatus(env, hwid, deviceFp) {
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

  let custom_os = abnormal;
  if (
    licensed &&
    userFile &&
    abnormal &&
    !isManualApprovedContent(userFile.content)
  ) {
    custom_os = false;
  }

  return { licensed, custom_os, userFile, stem };
}

export async function checkAbuse(
  env,
  ip,
  nonce,
  kvBinding = "ANKER_KV",
  ctx = null,
  request = null,
  body = null,
  route = "",
  worker = "anker-dlresolver",
) {
  const kv = env[kvBinding];
  if (!kv || !kvAbuseGuardForRoute(env, route)) return null;

  try {
    if (kvRateLimitWritesEnabled(env)) {
      const denied = await checkIpDenylist(kv, ip);
      if (denied) {
        if (ctx && request) {
          queueAbuseDiscordAlert(ctx, env, request, {
            worker,
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

    const nonceKey = `anker:nonce:${nonce}`;
    if (await kv.get(nonceKey)) {
      return json({ error: "replay" }, 409);
    }

    if (kvRateLimitWritesEnabled(env)) {
      const limit = parseInt(env.RATE_LIMIT_PER_MINUTE || "20", 10);
      const bucket = Math.floor(Date.now() / 60000);
      const rlKey = `anker:rl:${ip}:${bucket}`;
      const current = parseInt((await kv.get(rlKey)) || "0", 10);
      if (current >= limit) {
        const banned = await recordRateLimitStrike(kv, ip);
        if (banned && ctx && request) {
          queueAbuseDiscordAlert(ctx, env, request, {
            worker,
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
        kv.put(nonceKey, "1", { expirationTtl: 300 }),
        kv.put(rlKey, String(current + 1), { expirationTtl: 120 }),
      ]);
      return null;
    }

    await kv.put(nonceKey, "1", { expirationTtl: 300 });
    return null;
  } catch {
    return null;
  }
}

export async function maybeRecordHardwareSnapshot(
  env,
  ctx,
  stem,
  deviceFp,
  hwSnapshot,
  abnormal,
) {
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

  const lockKey = `anker:snap:lock:${stem}`;
  if (env.ANKER_KV && (await env.ANKER_KV.get(lockKey))) return false;
  if (env.ANKER_KV) {
    await env.ANKER_KV.put(lockKey, "1", { expirationTtl: 120 });
  }

  const ok = await writeHardwareSnapshot(env, stem, hwSnapshot, abnormal);
  if (env.ANKER_KV && ok) {
    await env.ANKER_KV.put(`anker:snap:done:${stem}`, "1");
  }
  return ok;
}
