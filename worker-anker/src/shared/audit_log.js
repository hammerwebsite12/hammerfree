/**
 * Async request audit trail (KV). Additive only — does not change API responses.
 */

import { normalizeHwidStem } from "./hwid_snapshot.js";
import { kvAuditEnabled } from "./kv_policy.js";

const AUDIT_TTL = 7 * 24 * 3600;

function utcDayKey(date = new Date()) {
  return date.toISOString().slice(0, 10);
}

/**
 * @param {ExecutionContext} ctx
 * @param {{ ANKER_KV?: KVNamespace }} env
 * @param {{ ip: string, hwid: string, route: string, gameId?: string, licensed?: boolean }} meta
 */
export function auditRequest(ctx, env, meta) {
  if (!kvAuditEnabled(env) || !env.ANKER_KV || !ctx) return;

  const stem = normalizeHwidStem(meta.hwid) || "unknown";
  const day = utcDayKey();
  const bucket = Math.floor(Date.now() / 60000);
  const ip = String(meta.ip || "unknown").slice(0, 80);
  const route = String(meta.route || "unknown").slice(0, 32);
  const gameId = meta.gameId ? String(meta.gameId).slice(0, 120) : "";

  const eventKey = `anker:audit:${route}:${day}:${stem}:${bucket}`;
  const ipKey = `anker:audit:ip:${day}:${ip}`;

  const payload = JSON.stringify({
    ip,
    stem,
    route,
    gameId: gameId || undefined,
    licensed: meta.licensed,
    t: Date.now(),
  });

  ctx.waitUntil(
    Promise.all([
      env.ANKER_KV.put(eventKey, payload, { expirationTtl: AUDIT_TTL }),
      env.ANKER_KV.get(ipKey).then((cur) => {
        const next = parseInt(cur || "0", 10) + 1;
        return env.ANKER_KV.put(ipKey, String(next), { expirationTtl: AUDIT_TTL });
      }),
    ]).catch(() => {}),
  );
}
