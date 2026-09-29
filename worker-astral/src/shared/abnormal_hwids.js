/** Known shared / fake SMBIOS UUIDs from custom Windows images. */

// Generic leaked / placeholder SMBIOS UUIDs only — never add a real customer's HWID here.
const KNOWN_ABNORMAL_STEMS = new Set([
  "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
  "00000000000000000000000000000000",
  "03000200040005000006000700080009",
  "2462F1A8EE0AB744A8875D6A8E5D2609",
]);

export const MANUAL_APPROVAL_MARKER = "Manual-Approved: quickplay-win";

export function normalizeHwidStem(value) {
  const cleaned = String(value || "")
    .replace(/-/g, "")
    .trim()
    .toUpperCase();
  if (!/^[0-9A-F]{32}$/.test(cleaned)) return null;
  return cleaned;
}

export function isAbnormalHwidStem(stem) {
  const normalized = normalizeHwidStem(stem);
  if (!normalized) return true;
  if (KNOWN_ABNORMAL_STEMS.has(normalized)) return true;
  if (/^(.)\1{31}$/.test(normalized)) return true;
  return false;
}

export function isManualApprovedContent(content) {
  return String(content || "").includes(MANUAL_APPROVAL_MARKER);
}

const DEVICE_FP_LINE = /^Device-FP:\s*([0-9a-fA-F]{64})\s*$/m;

export function extractDeviceFpFromContent(content) {
  const match = String(content || "").match(DEVICE_FP_LINE);
  return match ? match[1].toLowerCase() : null;
}

export function normalizeDeviceFp(value) {
  const cleaned = String(value || "").trim().toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(cleaned)) return null;
  return cleaned;
}

/** Abnormal HWID: manual marker + stored fingerprint must match this device. */
export function isManualApprovedForDevice(content, deviceFp) {
  if (!isManualApprovedContent(content)) return false;
  const stored = extractDeviceFpFromContent(content);
  const normalized = normalizeDeviceFp(deviceFp);
  if (!stored || !normalized) return false;
  return stored === normalized;
}
