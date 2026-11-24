import { AG_GRID_LOCALE_FR } from "@ag-grid-community/locale";
import { AllCommunityModule, ModuleRegistry, themeQuartz, type ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useMemo } from "react";

import type { DocumentRow } from "../api";
import { formatDate, formatDateTime } from "../format";

ModuleRegistry.registerModules([AllCommunityModule]);

const dateCell = { valueFormatter: (p: { value: unknown }) => formatDate(p.value as string | null) };

// Colonnes par défaut (docs/01_SPEC_FONCTIONNELLE.md § Le tableau).
export const COLUMNS: ColDef<DocumentRow>[] = [
  { field: "status", headerName: "Statut", pinned: "left", width: 130 },
  { field: "doc_type", headerName: "Type" },
  { field: "issuing_state", headerName: "Pays émetteur" },
  { field: "surname", headerName: "Nom" },
  { field: "given_names", headerName: "Prénoms" },
  { field: "surname_native", headerName: "Nom (écriture d'origine)" },
  { field: "usage_name", headerName: "Nom d'usage" },
  { field: "sex", headerName: "Sexe", width: 90 },
  { field: "date_of_birth", headerName: "Date de naissance", ...dateCell },
  { field: "place_of_birth", headerName: "Lieu de naissance" },
  { field: "nationality", headerName: "Nationalité" },
  { field: "document_number", headerName: "N° du document" },
  { field: "date_of_issue", headerName: "Date de délivrance", ...dateCell },
  { field: "date_of_expiry", headerName: "Date d'expiration", ...dateCell },
  { field: "authority", headerName: "Autorité de délivrance" },
  { field: "personal_number", headerName: "N° personnel" },
  { field: "mrz_valid", headerName: "MRZ" },
  { field: "confidence", headerName: "Confiance", width: 110 },
  { field: "alerts_json", headerName: "Alertes" },
  { field: "source_file", headerName: "Fichier source" },
  { field: "batch_id", headerName: "Lot", hide: true },
  {
    field: "created_at",
    headerName: "Analysé le",
    valueFormatter: (p) => formatDateTime(String(p.value ?? "")),
  },
];

// Police système, jamais de Google Fonts : aucune requête sortante (CLAUDE.md, règle 1).
const THEME = themeQuartz.withParams({ fontFamily: "inherit" });

const LOCALE = {
  ...AG_GRID_LOCALE_FR,
  noRowsToShow: "Aucun document analysé pour l'instant : déposez des fichiers ci-dessus.",
};

export function DocumentsGrid({ rows }: { rows: DocumentRow[] }) {
  const defaultColDef = useMemo<ColDef<DocumentRow>>(
    () => ({ sortable: true, filter: true, resizable: true, minWidth: 90 }),
    [],
  );
  return (
    <div className="h-[28rem] w-full">
      <AgGridReact<DocumentRow>
        theme={THEME}
        loadThemeGoogleFonts={false}
        rowData={rows}
        columnDefs={COLUMNS}
        defaultColDef={defaultColDef}
        localeText={LOCALE}
        getRowId={(p) => String(p.data.id)}
        rowSelection={{ mode: "multiRow" }}
      />
    </div>
  );
}
