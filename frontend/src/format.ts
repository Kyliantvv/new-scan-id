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
