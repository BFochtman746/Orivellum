/**
 * Safe UUID v4 generator — works in all browsers, secure context or not.
 *
 * `crypto.randomUUID()` is a secure-context API; Safari blocks it over plain
 * HTTP (e.g. Tailscale or LAN access without TLS).  This helper falls back to
 * a manual RFC 4122 v4 UUID built from `crypto.getRandomValues()`, which IS
 * available in non-secure contexts, or Math.random() as a last resort.
 */
export function randomUUID(): string {
  // Fast path: secure-context browsers
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  // Fallback: use getRandomValues (available over HTTP in most browsers)
  const buf = new Uint8Array(16);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    crypto.getRandomValues(buf);
  } else {
    // Last resort: Math.random (not cryptographically secure but functionally fine for IDs)
    for (let i = 0; i < buf.length; i++) buf[i] = Math.floor(Math.random() * 256);
  }

  // Set version bits (v4) and variant bits
  buf[6] = (buf[6] & 0x0f) | 0x40;
  buf[8] = (buf[8] & 0x3f) | 0x80;

  const hex = Array.from(buf).map(b => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}

/**
 * Copy text to clipboard, safe in non-secure contexts.
 *
 * `navigator.clipboard` requires HTTPS on Safari.  Falls back to the legacy
 * `execCommand('copy')` approach which works over HTTP.
 */
export async function copyToClipboard(text: string): Promise<void> {
  if (navigator?.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // fall through to execCommand
    }
  }

  // Legacy fallback
  const el = document.createElement("textarea");
  el.value = text;
  el.style.cssText = "position:fixed;top:-9999px;left:-9999px;opacity:0";
  document.body.appendChild(el);
  el.focus();
  el.select();
  document.execCommand("copy");
  document.body.removeChild(el);
}
