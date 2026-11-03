import { useEffect, useRef } from "react";

/** Diverging colour for a value in [-scale, scale]: blue for negative, orange for positive. */
function cellColor(v: number | null, scale: number): string {
  if (v === null) return "#1b1d21";
  const t = Math.max(-1, Math.min(1, v / scale));
  const base = [17, 18, 21];
  const target = t >= 0 ? [255, 122, 26] : [79, 141, 255];
  const a = Math.abs(t) ** 0.8;
  const mix = base.map((b, i) => Math.round(b + (target[i] - b) * a));
  return `rgb(${mix[0]},${mix[1]},${mix[2]})`;
}

interface Props {
  names: string[];
  matrix: (number | null)[][];
  scale: number;
  highlight: string;
  onHover?: (cell: { row: number; col: number } | null) => void;
  size?: number;
}

/** A correlation matrix drawn on a canvas: thousands of cells are too many for SVG. */
export function GridCanvas({ names, matrix, scale, highlight, onHover, size = 520 }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const n = names.length;

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const ratio = window.devicePixelRatio || 1;
    el.width = size * ratio;
    el.height = size * ratio;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, size, size);
    const cell = size / n;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        ctx.fillStyle = cellColor(i === j ? null : matrix[i][j], scale);
        ctx.fillRect(j * cell, i * cell, Math.ceil(cell), Math.ceil(cell));
      }
    }
    const t = names.indexOf(highlight);
    if (t >= 0) {
      ctx.strokeStyle = "#f4f4f2";
      ctx.lineWidth = 1;
      ctx.strokeRect(0.5, t * cell + 0.5, size - 1, cell - 1);
      ctx.strokeRect(t * cell + 0.5, 0.5, cell - 1, size - 1);
    }
  }, [names, matrix, scale, highlight, size, n]);

  const locate = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const col = Math.floor(((event.clientX - box.left) / box.width) * n);
    const row = Math.floor(((event.clientY - box.top) / box.height) * n);
    return row >= 0 && row < n && col >= 0 && col < n ? { row, col } : null;
  };

  return (
    <canvas
      ref={canvas}
      className="grid-canvas"
      style={{ width: "100%", maxWidth: size, aspectRatio: "1 / 1" }}
      onPointerMove={(e) => onHover?.(locate(e))}
      onPointerLeave={() => onHover?.(null)}
      role="img"
      aria-label="Correlation grid"
    />
  );
}

export function ColorScale({ scale, label }: { scale: number; label: string }) {
  const stops = [-1, -0.5, 0, 0.5, 1].map((t) => cellColor(t * scale, scale));
  return (
    <div className="colorscale">
      <span className="num small">{(-scale).toFixed(2)}</span>
      <span className="colorscale__bar" style={{ background: `linear-gradient(90deg, ${stops.join(",")})` }} />
      <span className="num small">+{scale.toFixed(2)}</span>
      <span className="muted small">{label}</span>
    </div>
  );
}
