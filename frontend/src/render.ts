// Shared formatting helpers for the page modules. No DOM, no side effects.

export function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
}

export function humanBytes(b: number): string {
  if (b < 1024) return `${b} B`
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`
  return `${(b / 1024 ** 3).toFixed(2)} GB`
}

export function humanDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00" // guards NaN duration before metadata loads
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const pad = (n: number) => n.toString().padStart(2, "0")
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`
}

export function humanBitrate(bps: number | null): string {
  if (!bps) return "—"
  if (bps < 1_000_000) return `${Math.round(bps / 1000)} kbps`
  return `${(bps / 1_000_000).toFixed(1)} Mbps`
}

const DAY = 86_400_000

export function relativeTime(iso: string | null, now: number = Date.now()): string {
  if (!iso) return ""
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ""
  const diff = now - t
  if (diff < 60_000) return "just now"
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (diff < DAY) return `${Math.floor(diff / 3_600_000)}h ago`
  if (diff < 7 * DAY) return `${Math.floor(diff / DAY)}d ago`
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

export function expiresIn(iso: string | null, now: number = Date.now()): string {
  if (!iso) return ""
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ""
  const diff = t - now
  if (diff <= 0) return "expired"
  if (diff < 3_600_000) return "expires soon"
  if (diff < DAY) return `expires in ${Math.floor(diff / 3_600_000)}h`
  return `expires in ${Math.floor(diff / DAY)}d`
}

export function siteHeader(): string {
  return `<header class="site-header">
    <a href="/" class="wordmark">tideo</a>
    <nav class="site-nav"><a href="/history" class="site-nav-link">History</a></nav>
  </header>`
}
