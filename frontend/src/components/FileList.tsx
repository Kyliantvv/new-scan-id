import { describeFile, formatBytes, type FileView, type Tone } from "../format";

export interface FileItem {
  key: string;
  name: string;
  size: number;
  view: FileView;
}

const TONE: Record<Tone, string> = {
  neutral: "text-slate-700",
  success: "text-emerald-700",
  warning: "text-amber-700",
  error: "text-red-700",
};

const BAR: Record<Tone, string> = {
  neutral: "bg-blue-600",
  success: "bg-emerald-600",
  warning: "bg-amber-500",
  error: "bg-red-600",
};

export function FileList({ items }: { items: FileItem[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white" aria-live="polite">
      {items.map((item) => {
        const d = describeFile(item.view);
        return (
          <li key={item.key} className="flex items-center gap-4 px-4 py-2 text-sm">
            <span className="min-w-0 flex-1 truncate" title={item.name}>
              {item.name}
            </span>
            <span className="w-16 shrink-0 text-right tabular-nums text-slate-500">{formatBytes(item.size)}</span>
            <span className="w-40 shrink-0">
              {d.percent !== null && (
                <span
                  className="block h-1.5 overflow-hidden rounded-full bg-slate-200"
                  role="progressbar"
                  aria-valuenow={d.percent}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`Progression de ${item.name}`}
                >
                  <span className={`block h-full ${BAR[d.tone]}`} style={{ width: `${d.percent}%` }} />
                </span>
              )}
            </span>
            <span className={`w-64 shrink-0 truncate ${TONE[d.tone]}`} title={d.label}>
              {d.label}
              {d.percent !== null && d.tone === "neutral" ? ` · ${d.percent} %` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
