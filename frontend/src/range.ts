export type Range = readonly [number, number];

export const PRESETS: { label: string; days?: number; ytd?: boolean }[] = [
  { label: "1M", days: 21 },
  { label: "3M", days: 63 },
  { label: "YTD", ytd: true },
  { label: "1Y", days: 252 },
  { label: "3Y", days: 756 },
  { label: "All" },
];

export function presetRange(dates: string[], label: string): Range {
  const last = dates.length - 1;
  const preset = PRESETS.find((p) => p.label === label);
  if (!preset || (!preset.days && !preset.ytd)) return [0, last];
  if (preset.ytd) {
    const year = dates[last].slice(0, 4);
    const first = dates.findIndex((d) => d.startsWith(year));
    return [Math.max(0, first), last];
  }
  return [Math.max(0, last - (preset.days ?? 0) + 1), last];
}
