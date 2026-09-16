/**
 * KV write budget controls for Cloudflare free tier (~1,000 writes/day).
 * Default lite mode keeps signing alive by skipping optional KV traffic.
 */

export function kvLiteMode(env) {
  return String(env.KV_LITE_MODE ?? "true").toLowerCase() !== "false";
}

export function kvAuditEnabled(env) {
  if (kvLiteMode(env)) return false;
  return String(env.ENABLE_KV_AUDIT || "").toLowerCase() === "true";
}

export function kvUsageStatsEnabled(env) {
  if (kvLiteMode(env)) return false;
  return String(env.ENABLE_KV_USAGE_STATS || "").toLowerCase() === "true";
}

export function kvRateLimitWritesEnabled(env) {
  return !kvLiteMode(env);
}

/** Lite mode: only /sign and /resolve use nonce replay KV (1 write each). */
export function kvAbuseGuardForRoute(env, route) {
  if (!kvLiteMode(env)) return true;
  const r = String(route || "");
  return r.includes("/sign") || r.includes("/resolve");
}
