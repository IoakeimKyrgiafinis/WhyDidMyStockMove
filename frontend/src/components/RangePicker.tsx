import { fmtDate } from "../lib";
import { PRESETS, presetRange, type Range } from "../range";

interface Props {
  dates: string[];
  range: Range;
  onChange: (range: Range) => void;
}

export function RangePicker({ dates, range, onChange }: Props) {
  const last = dates.length - 1;
  const [lo, hi] = range;
  const activePreset = PRESETS.find((p) => {
    const [a, b] = presetRange(dates, p.label);
    return a === lo && b === hi;
  })?.label;

  const left = (lo / Math.max(last, 1)) * 100;
  const right = (hi / Math.max(last, 1)) * 100;

  return (
    <div className="range">
      <div className="range__presets" role="group" aria-label="Period">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            type="button"
            className={p.label === activePreset ? "is-active" : undefined}
            onClick={() => onChange(presetRange(dates, p.label))}
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="range__slider">
        <div className="range__track" />
        <div className="range__fill" style={{ left: `${left}%`, width: `${right - left}%` }} />
        <input
          type="range"
          min={0}
          max={last}
          value={lo}
          aria-label="Period start"
          onChange={(e) => onChange([Math.min(Number(e.target.value), hi - 1), hi])}
        />
        <input
          type="range"
          min={0}
          max={last}
          value={hi}
          aria-label="Period end"
          onChange={(e) => onChange([lo, Math.max(Number(e.target.value), lo + 1)])}
        />
      </div>
      <div className="range__labels">
        <span>{fmtDate(dates[lo])}</span>
        <span>{hi - lo + 1} trading days</span>
        <span>{fmtDate(dates[hi])}</span>
      </div>
    </div>
  );
}
