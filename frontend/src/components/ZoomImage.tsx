import { useEffect, useRef, useState, type PointerEvent, type WheelEvent } from "react";

import type { Box } from "../api";

export interface Overlay {
  key: string;
  box: Box;
  active: boolean;
  tone: "ok" | "warn" | "error";
}

const STROKE = { ok: "#059669", warn: "#d97706", error: "#dc2626" } as const;

/**
 * Image zoomable (molette), déplaçable (glisser), tournable par quart de tour, avec les cadres
 * des champs en SVG dans le repère de l'image (ils suivent zoom et rotation).
 * Double-clic : retour à la vue d'ensemble.
 */
export function ZoomImage({
  src,
  width,
  height,
  rotation,
  overlays,
  label,
}: {
  src: string;
  width: number;
  height: number;
  rotation: number;
  overlays: Overlay[];
  label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 600, h: 500 });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setSize({ w: entry.contentRect.width, h: entry.contentRect.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, [src, rotation]);

  const quarter = rotation % 180 !== 0;
  const fit = Math.min(size.w / (quarter ? height : width), size.h / (quarter ? width : height)) * 0.96;
  const scale = fit * zoom;

  const onWheel = (e: WheelEvent<HTMLDivElement>) => {
    const next = Math.min(8, Math.max(1, zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    setZoom(next);
    if (next === 1) setPan({ x: 0, y: 0 });
  };
  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (d) setPan({ x: d.px + e.clientX - d.x, y: d.py + e.clientY - d.y });
  };
  const stop = () => {
    drag.current = null;
  };

  return (
    <div
      ref={ref}
      className="relative h-full min-h-[20rem] w-full cursor-grab touch-none overflow-hidden rounded-lg bg-slate-800 active:cursor-grabbing"
      onWheel={onWheel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stop}
      onPointerCancel={stop}
      onDoubleClick={() => {
        setZoom(1);
        setPan({ x: 0, y: 0 });
      }}
      role="img"
      aria-label={label}
    >
      <div
        className="absolute left-1/2 top-1/2"
        style={{
          width,
          height,
          transform: `translate(-50%, -50%) translate(${pan.x}px, ${pan.y}px) scale(${scale}) rotate(${rotation}deg)`,
          transformOrigin: "center",
        }}
      >
        <img src={src} width={width} height={height} alt="" draggable={false} className="select-none" />
        <svg className="pointer-events-none absolute inset-0" viewBox={`0 0 ${width} ${height}`}>
          {overlays.map((o) => (
            <rect
              key={o.key}
              x={o.box.x - 4}
              y={o.box.y - 4}
              width={o.box.w + 8}
              height={o.box.h + 8}
              rx={4}
              fill={o.active ? `${STROKE[o.tone]}22` : "none"}
              stroke={STROKE[o.tone]}
              strokeWidth={(o.active ? 4 : 2) / scale}
              opacity={o.active ? 1 : 0.6}
            />
          ))}
        </svg>
      </div>
    </div>
  );
}
