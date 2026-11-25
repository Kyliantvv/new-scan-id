import { AG_GRID_LOCALE_FR } from "@ag-grid-community/locale";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type CellValueChangedEvent,
  type ColDef,
  type GridApi,
  type ICellRendererParams,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { cropImageUrl, type DocumentRow } from "../api";
import {
  DATE_FIELDS,
  DOC_STATUS,
  DOC_TYPES,
  docStatusLabel,
  formatDate,
  formatDateTime,
  parseDisplayDate,
  TONE_CLASSES,
} from "../format";

ModuleRegistry.registerModules([AllCommunityModule]);

export const EDITABLE = new Set([
  "surname",
  "given_names",
  "usage_name",
  "sex",
  "date_of_birth",
  "place_of_birth",
  "nationality",
  "document_number",
  "date_of_issue",
  "date_of_expiry",
  "authority",
  "personal_number",
]);

const ALERTS: Record<string, string> = {
  EXPIRED: "Expiré",
  MINOR: "Mineur",
  EXPIRES_SOON: "Expire bientôt",
  LOW_QUALITY: "Qualité faible",
  DUPLICATE_DOCUMENT: "Doublon",
  UNUSUAL_NUMBER: "Numéro inhabituel",
  ISSUE_VALIDITY_UNUSUAL: "Durée de validité inhabituelle",
  ISSUE_DATE_INCONSISTENT: "Date de délivrance incohérente",
  MRZ_NAME_TRUNCATED: "Nom tronqué dans la MRZ",
};

const asList = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

function StatusCell(p: ICellRendererParams<DocumentRow>) {
  const s = DOC_STATUS[String(p.value)];
  const reasons = asList(p.data?.status_reasons_json)
    .map((r) => (r as { label?: string }).label)
    .filter(Boolean)
    .join("\n");
  return (
    <Link
      to={`/documents/${String(p.data?.id)}`}
      title={reasons || "Ouvrir l'écran de vérification"}
      className={`rounded px-2 py-0.5 text-xs font-medium ${TONE_CLASSES[s?.tone ?? "neutral"]}`}
    >
      {s?.label ?? String(p.value)}
    </Link>
  );
}

const dateCol = (field: string, headerName: string): ColDef<DocumentRow> => ({
  field,
  headerName,
  valueFormatter: (p) => formatDate(p.value as string | null),
  valueParser: (p) => (p.newValue ? parseDisplayDate(String(p.newValue)) : null),
  filterValueGetter: (p) => formatDate(p.data?.[field] as string | null),
});

// Colonnes par défaut (docs/01_SPEC_FONCTIONNELLE.md § Le tableau).
const BASE_COLUMNS: ColDef<DocumentRow>[] = [
  { field: "status", headerName: "Statut", pinned: "left", width: 140, cellRenderer: StatusCell, editable: false,
    filterValueGetter: (p) => docStatusLabel(p.data?.status) },
  { field: "doc_type", headerName: "Type", valueFormatter: (p) => DOC_TYPES[String(p.value)] ?? String(p.value ?? "") },
  { field: "issuing_state", headerName: "Pays émetteur", width: 120 },
  { field: "surname", headerName: "Nom" },
  { field: "given_names", headerName: "Prénoms" },
  { field: "surname_native", headerName: "Nom (écriture d'origine)", hide: true },
  { field: "usage_name", headerName: "Nom d'usage" },
  { field: "sex", headerName: "Sexe", width: 90 },
  dateCol("date_of_birth", "Date de naissance"),
  { field: "place_of_birth", headerName: "Lieu de naissance" },
  { field: "nationality", headerName: "Nationalité", width: 120 },
  { field: "document_number", headerName: "N° du document" },
  dateCol("date_of_issue", "Date de délivrance"),
  dateCol("date_of_expiry", "Date d'expiration"),
  { field: "authority", headerName: "Autorité de délivrance" },
  { field: "personal_number", headerName: "N° personnel" },
  { field: "mrz_valid", headerName: "MRZ", width: 100,
    valueFormatter: (p) => (p.value == null ? "Absente" : p.value ? "Valide" : "Invalide") },
  { field: "confidence", headerName: "Confiance", width: 110,
    valueFormatter: (p) => (p.value == null ? "" : String(Math.round(Number(p.value) * 100))) },
  { field: "alerts_json", headerName: "Alertes",
    valueFormatter: (p) => asList(p.value).map((a) => ALERTS[String(a)] ?? String(a)).join(", ") },
  { field: "source_file", headerName: "Fichier source",
    valueFormatter: (p) => (p.value ? `${String(p.value)} · p. ${Number(p.data?.page_index ?? 0) + 1}` : "") },
  { field: "batch_id", headerName: "Lot", hide: true },
  { field: "created_at", headerName: "Analysé le", valueFormatter: (p) => formatDateTime(String(p.value ?? "")) },
];

export const COLUMNS: ColDef<DocumentRow>[] = BASE_COLUMNS.map((c) => ({
  ...c,
  editable: c.editable ?? EDITABLE.has(String(c.field)),
}));

// Police système, jamais de Google Fonts : aucune requête sortante (CLAUDE.md, règle 1).
const THEME = themeQuartz.withParams({ fontFamily: "inherit" });

const LOCALE = {
  ...AG_GRID_LOCALE_FR,
  noRowsToShow: "Aucun document analysé pour l'instant : déposez des fichiers ci-dessus.",
};

export function DocumentsGrid({
  rows,
  onEdit,
  onReady,
}: {
  rows: DocumentRow[];
  onEdit: (id: string, field: string, value: string | null) => Promise<void>;
  onReady?: (api: GridApi<DocumentRow>) => void;
}) {
  const [preview, setPreview] = useState<{ crop: string; x: number; y: number } | null>(null);
  const defaultColDef = useMemo<ColDef<DocumentRow>>(
    () => ({ sortable: true, filter: true, resizable: true, minWidth: 90 }),
    [],
  );
  const onCellValueChanged = (e: CellValueChangedEvent<DocumentRow>) => {
    const field = e.colDef.field;
    if (!field || !e.data || e.newValue === e.oldValue) return;
    const value = e.newValue == null || String(e.newValue).trim() === "" ? null : String(e.newValue).trim();
    void onEdit(String(e.data.id), field, DATE_FIELDS.has(field) && value ? parseDisplayDate(value) : value).catch(
      () => e.node.setDataValue(field, e.oldValue),
    );
  };
  return (
    <div className="relative h-[32rem] w-full" onMouseLeave={() => setPreview(null)}>
      <AgGridReact<DocumentRow>
        theme={THEME}
        loadThemeGoogleFonts={false}
        rowData={rows}
        columnDefs={COLUMNS}
        defaultColDef={defaultColDef}
        localeText={LOCALE}
        getRowId={(p) => String(p.data.id)}
        rowSelection={{ mode: "multiRow" }}
        singleClickEdit={false}
        stopEditingWhenCellsLoseFocus
        onCellValueChanged={onCellValueChanged}
        onGridReady={(e) => onReady?.(e.api)}
        onCellMouseOver={(e) => {
          const crop = e.data?.preview_crop_id;
          const ev = e.event as MouseEvent | null | undefined;
          setPreview(crop && ev ? { crop: String(crop), x: ev.clientX, y: ev.clientY } : null);
        }}
      />
      {preview && (
        <img
          src={cropImageUrl(preview.crop)}
          alt="Aperçu du recadrage"
          className="pointer-events-none fixed z-50 w-72 rounded border border-slate-300 bg-white shadow-lg"
          style={{ left: Math.min(preview.x + 16, window.innerWidth - 300), top: preview.y + 16 }}
        />
      )}
    </div>
  );
}
