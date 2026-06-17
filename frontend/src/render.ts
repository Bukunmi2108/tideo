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
