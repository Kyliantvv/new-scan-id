// Mise en forme et libellés de l'interface (en français).

import type { JobState, UploadResult } from "./api";

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} o`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} Ko`;
  return `${(n / (1024 * 1024)).toFixed(1).replace(".", ",")} Mo`;
}

/** Date ISO 8601 → JJ/MM/AAAA HH:MM, heure locale (CLAUDE.md § Conventions : affichage JJ/MM/AAAA). */
export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (v: number) => String(v).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Date ISO AAAA-MM-JJ → JJ/MM/AAAA, sans conversion de fuseau. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "";
}

const JOB_LABELS: Record<JobState["status"], string> = {
  queued: "En attente",
  running: "En cours",
  done: "Terminé",
  failed: "Échec",
};

const ERROR_LABELS: Record<string, string> = {
  UNSUPPORTED_FORMAT: "Format non pris en charge (PDF, JPG ou PNG attendu)",
  FILE_TOO_LARGE: "Fichier trop volumineux (50 Mo maximum)",
  EMPTY_FILE: "Fichier vide",
  WORKER_LOST: "Analyse interrompue trop souvent",
  FILE_MISSING_ON_DISK: "Fichier introuvable sur le serveur",
  FILE_CHECKSUM_MISMATCH: "Fichier altéré sur le serveur",
  INTERNAL_ERROR: "Erreur interne pendant l'analyse",
  NETWORK: "Envoi interrompu",
};

export function errorLabel(code: string | null | undefined): string {
  if (!code) return "";
  return ERROR_LABELS[code] ?? `Erreur (${code})`;
}

export type FileView =
  | { kind: "uploading"; fraction: number }
  | { kind: "rejected"; error: string }
  | { kind: "duplicate" }
  | { kind: "job"; job: JobState };

/** Libellé et pourcentage affichés pour un fichier, de l'envoi à la fin de l'analyse. */
export function describeFile(view: FileView): { label: string; percent: number | null; tone: Tone } {
  switch (view.kind) {
    case "uploading":
      return { label: "Envoi", percent: Math.round(view.fraction * 100), tone: "neutral" };
    case "rejected":
      return { label: errorLabel(view.error), percent: null, tone: "error" };
    case "duplicate":
      return { label: "Doublon : fichier déjà présent dans le lot", percent: null, tone: "warning" };
    case "job": {
      const { job } = view;
      if (job.status === "failed") return { label: errorLabel(job.error_code), percent: null, tone: "error" };
      const percent = job.status === "done" ? 100 : Math.round(job.progress * 100);
      const tone: Tone = job.status === "done" ? "success" : "neutral";
      return { label: JOB_LABELS[job.status], percent, tone };
    }
  }
}

export type Tone = "neutral" | "success" | "warning" | "error";

export function viewFromUpload(result: UploadResult): FileView {
  if (result.status === "rejected") return { kind: "rejected", error: result.error_code ?? "UNKNOWN" };
  if (result.status === "duplicate") return { kind: "duplicate" };
  return {
    kind: "job",
    job: { id: result.job_id ?? "", status: "queued", progress: 0, stage: null, error_code: null, attempts: 0 },
  };
}

// Statuts d'un document (docs/01_SPEC_FONCTIONNELLE.md § Statuts d'un document).
export const DOC_STATUS: Record<string, { label: string; tone: Tone }> = {
  pending: { label: "En attente", tone: "neutral" },
  processing: { label: "En cours", tone: "neutral" },
  auto_validated: { label: "Validé (auto)", tone: "success" },
  to_review: { label: "À vérifier", tone: "warning" },
  manual_validated: { label: "Validé (manuel)", tone: "success" },
  failed: { label: "Échec", tone: "error" },
  duplicate: { label: "Doublon", tone: "warning" },
};

export function docStatusLabel(status: unknown): string {
  return DOC_STATUS[String(status)]?.label ?? String(status ?? "");
}

export const DOC_TYPES: Record<string, string> = {
  passport: "Passeport",
  id_card: "Carte d'identité",
  residence_permit: "Titre de séjour",
  visa: "Visa",
  travel_document: "Document de voyage",
  other: "Autre",
};

export const FIELD_STATUS: Record<string, { label: string; tone: Tone }> = {
  ok: { label: "Prouvé", tone: "success" },
  repaired: { label: "Réparé et prouvé", tone: "success" },
  uncertain: { label: "Non prouvé", tone: "warning" },
  conflict: { label: "Conflit", tone: "error" },
  missing: { label: "Absent", tone: "neutral" },
  manual: { label: "Saisi", tone: "success" },
};

export const SOURCE_LABELS: Record<string, string> = {
  mrz: "MRZ",
  viz: "OCR",
  barcode: "Code-barres",
  vlm: "IA",
  human: "Humain",
};

export const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700",
  success: "bg-emerald-100 text-emerald-800",
  warning: "bg-amber-100 text-amber-900",
  error: "bg-red-100 text-red-800",
};

/** Alternative suivante (sens +1) ou précédente (−1) dans la liste valeur + candidats. */
export function cycleValue(current: string, options: string[], step: 1 | -1): string {
  const list = [...new Set(options.filter((o) => o !== ""))];
  if (list.length === 0) return current;
  const i = list.indexOf(current);
  if (i < 0) return list[step === 1 ? 0 : list.length - 1] ?? current;
  return list[(i + step + list.length) % list.length] ?? current;
}

/** Document suivant à vérifier après `currentId` (ordre du tableau), sinon le premier. */
export function nextToReview(ids: string[], currentId: string): string | null {
  const others = ids.filter((id) => id !== currentId);
  if (others.length === 0) return null;
  const i = ids.indexOf(currentId);
  const after = i < 0 ? [] : ids.slice(i + 1);
  return after[0] ?? others[0] ?? null;
}

/** JJ/MM/AAAA saisi par l'opérateur → AAAA-MM-JJ ; autre texte inchangé. */
export function parseDisplayDate(text: string): string {
  const m = /^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\s*$/.exec(text);
  if (!m) return text.trim();
  const [, d, mo, y] = m;
  return `${y}-${mo!.padStart(2, "0")}-${d!.padStart(2, "0")}`;
}

export const DATE_FIELDS = new Set(["date_of_birth", "date_of_issue", "date_of_expiry"]);
