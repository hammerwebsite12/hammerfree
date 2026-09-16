/**
 * Temporary IP denylist after repeated rate-limit hits (Worker-only).
 * Returns the same { error: "rate_limited" } shape so all client versions behave identically.
 */

const STRIKES_BEFORE_DENY = 3;
const DENY_TTL_SECONDS = 3600;

function hourBucket(date = new Date()) {
  return date.toISOString().slice(0, 13).replace("T", "-");
}

function denyKey(ip, prefix = "") {
  return `${prefix}deny:ip:${ip}`;
}

function strikeKey(ip, prefix = "") {
  return `${prefix}strike:rl:${hourBucket()}:${ip}`;
}

/**
 * @param {KVNamespace} kv
 * @returns {Promise<Response|null>}
 */
export async function checkIpDenylist(kv, ip, prefix = "") {
  if (!kv || !ip) return null;
  if (await kv.get(denyKey(ip, prefix))) {
    return new Response(JSON.stringify({ error: "rate_limited", wait: 60 }), {
      status: 429,
      headers: { "content-type": "application/json; charset=utf-8" },
    });
  }
  return null;
}

/**
 * Call when an IP hits per-minute rate limit. After 3 strikes in one UTC hour → 1h deny.
 * @returns {Promise<boolean>} true if IP was newly denylisted
 */
export async function recordRateLimitStrike(kv, ip, prefix = "") {
  if (!kv || !ip) return false;
  const key = strikeKey(ip, prefix);
  const next = parseInt((await kv.get(key)) || "0", 10) + 1;
  await kv.put(key, String(next), { expirationTtl: 7200 });
  if (next < STRIKES_BEFORE_DENY) return false;
  await kv.put(denyKey(ip, prefix), String(Date.now()), {
    expirationTtl: DENY_TTL_SECONDS,
  });
  return true;
}
