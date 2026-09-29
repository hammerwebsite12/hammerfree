/**
 * One-time hardware snapshot append to quickplayusr .user files.
 */

import {
  isAbnormalHwidStem,
  isManualApprovedContent,
  isManualApprovedForDevice,
  normalizeDeviceFp,
  normalizeHwidStem,
} from "./abnormal_hwids.js";

export const SNAPSHOT_MARKER = "Hardware-Snapshot-Recorded:";
const MAX_SNAPSHOT_CHARS = 2500;

function githubHeaders(pat) {
  const value = String(pat || "").trim();
  const authorization = value.startsWith("github_pat_")
    ? `Bearer ${value}`
    : `token ${value}`;
  return {
    "User-Agent": "QuickPlayDlResolver/1.0",
    Authorization: authorization,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

export function decodeGithubFileContent(data) {
  if (!data || typeof data.content !== "string") return "";
  try {
    const binary = atob(data.content.replace(/\n/g, ""));
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  } catch {
    throw new Error("license_check_failed:bad_content");
  }
}

function encodeGithubFileContent(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

export function hasHardwareSnapshot(content) {
  return String(content || "").includes(SNAPSHOT_MARKER);
}

export function sanitizeSnapshot(value) {
  const text = String(value || "")
    .replace(/\r\n/g, "\n")
    .replace(/[^\x09\x0A\x0D\x20-\x7E]/g, "")
    .trim();
  if (!text || text.length > MAX_SNAPSHOT_CHARS) return null;
  if (!text.includes("Device-FP:")) return null;
  return text;
}

export function snapshotSummary(snapshot) {
  const lines = String(snapshot || "").split("\n");
  const pick = (prefix) => {
    const line = lines.find((l) => l.startsWith(prefix));
    return line ? line.slice(prefix.length).trim() : "";
  };
  return {
    computer: pick("Computer:"),
    board: pick("Board:"),
    cpu: pick("CPU:"),
    ram: pick("RAM:"),
    os: pick("OS:"),
    deviceFp: pick("Device-FP:"),
    hwidStem: pick("HWID-Stem:"),
  };
}

export function appendSnapshotToUserFile(existingContent, snapshotText, recordedAtIso) {
  const base = String(existingContent || "").trimEnd();
  const block =
    `\n\n${SNAPSHOT_MARKER} ${recordedAtIso}\n` +
    `${snapshotText.trim()}\n`;
  return `${base}${block}`;
}

export async function fetchHwidUserFile(env, stem) {
  const owner = env.QUICKPLAY_HWID_OWNER;
  const repo = env.QUICKPLAY_HWID_REPO;
  const pat = env.QUICKPLAY_HWID_PAT;
  if (!owner || !repo || !pat) {
    throw new Error("license_check_unconfigured");
  }

  const fileName = `${stem}.user`;
  const url = `https://api.github.com/repos/${owner}/${repo}/contents/${fileName}`;
  const response = await fetch(url, {
    method: "GET",
    headers: githubHeaders(pat),
  });

  if (response.status === 404) {
    return null;
  }
  if (response.status !== 200) {
    throw new Error(`license_check_failed:${response.status}`);
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("license_check_failed:bad_json");
  }

  return {
    fileName,
    sha: payload.sha,
    content: decodeGithubFileContent(payload),
  };
}

export function isLicensedUserFile(content, stem, deviceFp) {
  if (!content) return false;
  if (!isAbnormalHwidStem(stem)) return true;
  if (!isManualApprovedContent(content)) return true;
  return isManualApprovedForDevice(content, deviceFp);
}

export async function writeHardwareSnapshot(env, stem, snapshotText, abnormal) {
  const owner = env.QUICKPLAY_HWID_OWNER;
  const repo = env.QUICKPLAY_HWID_REPO;
  const pat = env.QUICKPLAY_HWID_PAT;
  if (!owner || !repo || !pat) return false;

  const file = await fetchHwidUserFile(env, stem);
  if (!file || hasHardwareSnapshot(file.content)) return false;

  const sanitized = sanitizeSnapshot(snapshotText);
  if (!sanitized) return false;

  const recordedAt = new Date().toISOString();
  const newContent = appendSnapshotToUserFile(file.content, sanitized, recordedAt);
  const summary = snapshotSummary(sanitized);
  const commitSummary = [
    summary.computer,
    summary.board,
    summary.cpu,
    summary.ram,
    summary.os,
  ]
    .filter(Boolean)
    .join(" | ");

  const url = `https://api.github.com/repos/${owner}/${repo}/contents/${file.fileName}`;
  const response = await fetch(url, {
    method: "PUT",
    headers: {
      ...githubHeaders(pat),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      message:
        `Record hardware snapshot ${file.fileName}` +
        (commitSummary ? ` — ${commitSummary}` : ""),
      content: encodeGithubFileContent(newContent),
      sha: file.sha,
    }),
  });

  if (!response.ok) return false;

  await sendDiscordFirstConnect(env, {
    stem,
    abnormal,
    summary,
    recordedAt,
  });
  return true;
}

async function sendDiscordFirstConnect(env, { stem, abnormal, summary, recordedAt }) {
  const webhookUrl = env.DISCORD_WEBHOOK_URL;
  if (!webhookUrl) return;

  const kind = abnormal ? "Custom OS / shared HWID" : "Normal";
  const fpShort = summary.deviceFp
    ? `${summary.deviceFp.slice(0, 12)}…`
    : "n/a";

  const content =
    `**QuickPlay — first licensed download (hardware recorded)**\n` +
    `**Type:** ${kind}\n` +
    `**HWID:** \`${stem}\`\n` +
    `**Device-FP:** \`${fpShort}\`\n` +
    `**Computer:** ${summary.computer || "?"}\n` +
    `**Board:** ${summary.board || "?"}\n` +
    `**CPU:** ${summary.cpu || "?"}\n` +
    `**RAM:** ${summary.ram || "?"}\n` +
    `**OS:** ${summary.os || "?"}\n` +
    `**Recorded (UTC):** \`${recordedAt}\``;

  try {
    await fetch(webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
  } catch {
    // non-fatal
  }
}

export { isAbnormalHwidStem, normalizeDeviceFp, normalizeHwidStem };
