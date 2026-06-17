// Preset ladder + pure picker/estimate logic. recommended_presets from the
// backend is the authority on what's allowed; this only renders the choice.

export interface Rung {
  preset: string
  label: string
  height: number
  resolution: string
}

// Highest-first, matching the backend catalog order.
export const LADDER: Rung[] = [
  { preset: "1080p", label: "1080p", height: 1080, resolution: "1920x1080" },
  { preset: "720p", label: "720p", height: 720, resolution: "1280x720" },
  { preset: "480p", label: "480p", height: 480, resolution: "854x480" },
  { preset: "360p", label: "360p", height: 360, resolution: "640x360" },
]

// Conservative encode-time ratios; Phase 8.3 replaces these with measured values.
export const ESTIMATE_RATIOS: Record<string, number> = {
  "1080p": 0.5,
  "720p": 0.35,
  "480p": 0.22,
  "360p": 0.15,
}

export interface PickerRow extends Rung {
  available: boolean
  checked: boolean
  reason: string | null
}

export function buildPicker(recommended: string[], sourceHeight: number): PickerRow[] {
  const allowed = new Set(recommended)
  return LADDER.map((rung) => {
    const available = allowed.has(rung.preset)
    return {
      ...rung,
      available,
      checked: available,
      reason: available ? null : `source is ${sourceHeight}p — no upscale`,
    }
  })
}

// Sum (not max) per-preset estimates — renditions contend for cores, so this stays honest-pessimistic.
export function estimateSeconds(presets: string[], durationSeconds: number): number {
  return presets.reduce((acc, p) => acc + durationSeconds * (ESTIMATE_RATIOS[p] ?? 0.5), 0)
}
