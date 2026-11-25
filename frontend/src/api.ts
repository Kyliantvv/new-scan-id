// Client de l'API IDReader (même origine). Le jeton CSRF accompagne toute requête qui modifie
// des données (docs/05_SECURITE_RGPD.md § Accès).

export type Role = "operator" | "admin";

export interface Me {
  id: string;
  email: string;
  role: Role;
  csrf_token: string;
}

export interface JobState {
  id: string;
  status: "queued" | "running" | "done" | "failed";
  progress: number;
  stage: string | null;
  error_code: string | null;
  attempts: number;
}

export interface BatchSummary {
  id: string;
  name: string;
  status: string;
  created_at: string;
  file_count: number;
  job_counts: Record<string, number>;
}

export interface BatchFile {
  id: string;
  original_name: string;
  mime: string;
  size_bytes: number;
  upload_order: number;
  duplicate_of_id: string | null;
  created_at: string;
  job: JobState | null;
}

export interface BatchDetail extends BatchSummary {
  files: BatchFile[];
}

export interface UploadResult {
  name: string;
  status: "queued" | "duplicate" | "rejected";
  file_id: string | null;
  job_id: string | null;
  duplicate_of_id: string | null;
  error_code: string | null;
}

export type DocumentRow = Record<string, unknown>;

export interface Reason {
  code: string;
  field: string | null;
  label: string;
}

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FieldDetail {
  name: string;
  label: string;
  value: string | null;
  value_native: string | null;
  status: "ok" | "repaired" | "uncertain" | "conflict" | "missing" | "manual";
  confidence: number | null;
  sources: string[];
  reasons: string[];
  candidates: string[];
  bbox: Box | null;
  side: "front" | "back" | null;
}

export interface CropInfo {
  id: string;
  width: number;
  height: number;
  page_id: string;
  file_id: string;
  file_name: string;
  page_index: number;
  rotation: number;
}

export interface MrzCheck {
  name: string;
  line: number;
  index: number;
  ok: boolean;
}

export interface MrzDetail {
  format: string;
  lines: string[];
  checks: MrzCheck[];
  changed: { line: number; index: number }[];
  side: "front" | "back";
}

export interface DocumentDetail {
  id: string;
  batch_id: string;
  status: string;
  reasons: Reason[];
  alerts: string[];
  alert_labels: string[];
  doc_type: string | null;
  issuing_state: string | null;
  mrz_valid: boolean | null;
  confidence: number | null;
  duplicate_of_id: string | null;
  reviewed_at: string | null;
  fields: FieldDetail[];
  front: CropInfo | null;
  back: CropInfo | null;
  mrz: MrzDetail | null;
  golden_enabled: boolean;
}

export interface ExportRequest {
  format: "xlsx" | "csv";
  batch?: string;
  status?: string;
  ids?: string[];
  columns?: string[];
  include_status?: boolean;
}

export interface AppSettings {
  auto_validate_threshold: number;
  retention_images_days: number;
  retention_data_days: number;
  golden_enabled: boolean;
}

export interface UserRow {
  id: string;
  email: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export interface Stats {
  documents: number;
  by_status: Record<string, number>;
  automation_pct: number | null;
  failures_by_reason: Record<string, number>;
  by_country: Record<string, number>;
  by_type: Record<string, number>;
  most_corrected_fields: [string, number][];
  mrz_confusions: [string, string, number][];
  avg_ms_per_document: number | null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

export function buildHeaders(method: string, body: unknown, token: string | null): Headers {
  const headers = new Headers({ Accept: "application/json" });
  if (body !== undefined && !(body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (!SAFE.has(method.toUpperCase()) && token) {
    headers.set("X-CSRF-Token", token);
  }
  return headers;
}

export async function errorFromResponse(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as { detail?: { code?: string; message?: string } };
    const code = body.detail?.code ?? "HTTP_" + res.status;
    return new ApiError(res.status, code, body.detail?.message ?? messageForStatus(res.status));
  } catch {
    return new ApiError(res.status, "HTTP_" + res.status, messageForStatus(res.status));
  }
}

function messageForStatus(status: number): string {
  if (status === 401) return "Session expirée : reconnectez-vous.";
  if (status === 403) return "Action non autorisée.";
  if (status >= 500) return "Le serveur ne répond pas correctement. Réessayez dans un instant.";
  return "La requête n'a pas abouti.";
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: buildHeaders(method, body, csrfToken),
    body:
      body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
  });
  if (!res.ok) throw await errorFromResponse(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  me: () => request<Me>("GET", "/api/me"),
  login: (email: string, password: string) =>
    request<Me>("POST", "/api/auth/login", { email, password }),
  logout: () => request<void>("POST", "/api/auth/logout"),
  listBatches: () => request<BatchSummary[]>("GET", "/api/batches"),
  createBatch: (name: string) => request<BatchSummary>("POST", "/api/batches", { name }),
  getBatch: (id: string) => request<BatchDetail>("GET", `/api/batches/${encodeURIComponent(id)}`),
  listDocuments: (batchId: string, status?: string) =>
    request<DocumentRow[]>(
      "GET",
      `/api/documents?batch=${encodeURIComponent(batchId)}` +
        (status ? `&status=${encodeURIComponent(status)}` : ""),
    ),
  getDocument: (id: string) => request<DocumentDetail>("GET", `/api/documents/${encodeURIComponent(id)}`),
  patchFields: (id: string, fields: Record<string, string | null>) =>
    request<DocumentDetail>("PATCH", `/api/documents/${encodeURIComponent(id)}/fields`, { fields }),
  validate: (id: string, fields: Record<string, string | null> = {}) =>
    request<DocumentDetail>("POST", `/api/documents/${encodeURIComponent(id)}/validate`, { fields }),
  reject: (id: string) => request<DocumentDetail>("POST", `/api/documents/${encodeURIComponent(id)}/reject`),
  reprocess: (id: string) => request<void>("POST", `/api/documents/${encodeURIComponent(id)}/reprocess`),
  deleteDocument: (id: string) => request<void>("DELETE", `/api/documents/${encodeURIComponent(id)}`),
  addToGolden: (id: string) => request<void>("POST", `/api/documents/${encodeURIComponent(id)}/golden`),
  getSettings: () => request<AppSettings>("GET", "/api/admin/settings"),
  putSettings: (s: AppSettings) => request<AppSettings>("PUT", "/api/admin/settings", s),
  listUsers: () => request<UserRow[]>("GET", "/api/admin/users"),
  createUser: (email: string, password: string, role: Role) =>
    request<UserRow>("POST", "/api/admin/users", { email, password, role }),
  updateUser: (id: string, patch: { role?: Role; is_active?: boolean; password?: string }) =>
    request<UserRow>("PATCH", `/api/admin/users/${encodeURIComponent(id)}`, patch),
  stats: (batch?: string) =>
    request<Stats>("GET", "/api/admin/stats" + (batch ? `?batch=${encodeURIComponent(batch)}` : "")),
};

export const cropImageUrl = (id: string) => `/api/crops/${encodeURIComponent(id)}/image`;

/** Export côté serveur ; le fichier est téléchargé par le navigateur (blob). */
export async function downloadExport(req: ExportRequest): Promise<void> {
  const res = await fetch("/api/exports", {
    method: "POST",
    credentials: "same-origin",
    headers: buildHeaders("POST", req, csrfToken),
    body: JSON.stringify(req),
  });
  if (!res.ok) throw await errorFromResponse(res);
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `export.${req.format}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Dépose un fichier avec suivi de l'envoi (XMLHttpRequest : fetch ne donne pas la progression). */
export function uploadFile(
  batchId: string,
  file: File,
  onProgress: (fraction: number) => void,
): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/batches/${encodeURIComponent(batchId)}/files`);
    xhr.withCredentials = true;
    if (csrfToken) xhr.setRequestHeader("X-CSRF-Token", csrfToken);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onerror = () => reject(new ApiError(0, "NETWORK", "Envoi interrompu : vérifiez la connexion."));
    xhr.onload = () => {
      if (xhr.status === 201) {
        const results = JSON.parse(xhr.responseText) as UploadResult[];
        const first = results[0];
        if (first) resolve(first);
        else reject(new ApiError(xhr.status, "EMPTY_RESPONSE", "Réponse vide du serveur."));
        return;
      }
      let code = "HTTP_" + xhr.status;
      let message = messageForStatus(xhr.status);
      try {
        const body = JSON.parse(xhr.responseText) as { detail?: { code?: string; message?: string } };
        code = body.detail?.code ?? code;
        message = body.detail?.message ?? message;
      } catch {
        /* réponse non JSON */
      }
      reject(new ApiError(xhr.status, code, message));
    };
    const form = new FormData();
    form.append("files", file, file.name);
    xhr.send(form);
  });
}
