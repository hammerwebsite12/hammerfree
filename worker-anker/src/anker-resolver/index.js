/**
 * anker-resolver — license gate for AnkerGames builds.
 *
 * Dedicated Worker (does NOT touch playzip dl-resolver).
 *
 *   GET  /              -> ok
 *   POST /license/check -> { licensed, custom_os? }
 *
 * Same HWID userbase + HMAC auth as playzip dl-resolver.
 */

import { auditRequest } from "../shared/audit_log.js";
import {
  abuseReasonFromResponse,
  queueAbuseDiscordAlert,
  trackHourlyVolumeAlert,
} from "../shared/abuse_alert.js";
import {
  checkAbuse,
  clientIp,
  json,
  normalizeDeviceFp,
  normalizeHwid,
  resolveLicenseStatus,
  verifyLicenseCheckRequest,
} from "../shared/auth.js";

function alertOnAbuse(ctx, env, request, body, route, abuse) {
  const reason = abuseReasonFromResponse(abuse);
  if (!reason) return;
  queueAbuseDiscordAlert(ctx, env, request, {
    worker: "anker-resolver",
    route,
    reason,
    hwid: body?.hwid,
    device_fp: body?.device_fp,
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/") {
      return new Response("ok", { status: 200 });
    }

    if (request.method !== "POST" || url.pathname !== "/license/check") {
      return json({ error: "not_found" }, 404);
    }

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
      "ANKER_KV",
      ctx,
      request,
      body,
      "POST /license/check",
      "anker-resolver",
    );
    if (abuse) {
      alertOnAbuse(ctx, env, request, body, "POST /license/check", abuse);
      return abuse;
    }

    const hwid = normalizeHwid(body.hwid);
    const deviceFp = normalizeDeviceFp(body.device_fp);
    const status = await resolveLicenseStatus(env, hwid, deviceFp);
    if (status.error) return status.error;

    auditRequest(ctx, env, {
      ip,
      hwid,
      route: "license_check",
      licensed: status.licensed,
    });
    trackHourlyVolumeAlert(ctx, env, request, {
      ip,
      hwid,
      route: "license_check",
      licensed: status.licensed,
      worker: "anker-resolver",
    });

    return json({
      licensed: status.licensed,
      custom_os: status.custom_os,
    });
  },
};
