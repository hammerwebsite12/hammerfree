/**
 * anker-dlresolver — licensed AnkerGames CDN URL resolver.
 *
 * Dedicated Worker (does NOT touch playzip dl-resolver).
 *
 *   GET  /         -> ok
 *   POST /resolve  { game_id, hwid, device_fp, ts, nonce, sig, token, hw_snapshot? }
 *     game_id = Anker slug (e.g. project-zomboid)
 *     sig = HMAC_SHA256(SIGNING_SECRET, "{ts}.{nonce}.{game_id}.{hwid}.{device_fp}")
 *   -> 200 { download_url, snapshot_recorded?: true }
 *   -> 403 { error: "license_required", custom_os?: bool }
 */

import {
  checkAbuse,
  clientIp,
  json,
  maybeRecordHardwareSnapshot,
  normalizeDeviceFp,
  normalizeHwid,
  resolveLicenseStatus,
  verifyResolveRequest,
} from "../shared/auth.js";
import {
  mapUpstreamError,
  resolveAnkerDownloadUrl,
} from "../shared/anker_upstream.js";
import { auditRequest } from "../shared/audit_log.js";
import {
  abuseReasonFromResponse,
  queueAbuseDiscordAlert,
  trackHourlyVolumeAlert,
} from "../shared/abuse_alert.js";
import {
  hasHardwareSnapshot,
  sanitizeSnapshot,
} from "../shared/hwid_snapshot.js";

function alertOnAbuse(ctx, env, request, body, route, abuse, worker, extra = {}) {
  const reason = abuseReasonFromResponse(abuse);
  if (!reason) return;
  queueAbuseDiscordAlert(ctx, env, request, {
    worker,
    route,
    reason,
    hwid: body?.hwid,
    device_fp: body?.device_fp,
    game_id: body?.game_id,
    ...extra,
  });
}

function recordAudit(ctx, env, request, meta) {
  auditRequest(ctx, env, meta);
  trackHourlyVolumeAlert(ctx, env, request, meta);
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/") {
      return new Response("ok", { status: 200 });
    }

    if (request.method !== "POST" || url.pathname !== "/resolve") {
      return json({ error: "not_found" }, 404);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "bad_json" }, 400);
    }

    const bad = await verifyResolveRequest(env, body);
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
      "POST /resolve",
      "anker-dlresolver",
    );
    if (abuse) {
      alertOnAbuse(ctx, env, request, body, "POST /resolve", abuse, "anker-dlresolver");
      return abuse;
    }

    const hwid = normalizeHwid(body.hwid);
    const deviceFp = normalizeDeviceFp(body.device_fp);
    const status = await resolveLicenseStatus(env, hwid, deviceFp);
    if (status.error) return status.error;

    recordAudit(ctx, env, request, {
      ip,
      hwid,
      route: "resolve",
      gameId: body.game_id,
      licensed: status.licensed,
      worker: "anker-dlresolver",
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

    const slug = String(body.game_id).trim();
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

    try {
      const result = await resolveAnkerDownloadUrl(env, slug);
      if (result.client_resolve) {
        const responseBody = { client_resolve: true };
        if (result.gate_url) {
          responseBody.gate_url = result.gate_url;
        }
        if (snapshotRecorded) {
          responseBody.snapshot_recorded = true;
        }
        return json(responseBody);
      }
      const responseBody = { download_url: result.download_url };
      if (snapshotRecorded) {
        responseBody.snapshot_recorded = true;
      }
      return json(responseBody);
    } catch (err) {
      const mapped = mapUpstreamError(err);
      return json(mapped.body, mapped.status);
    }
  },
};
