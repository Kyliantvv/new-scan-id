import { useQueryClient } from "@tanstack/react-query";
import type { GridApi } from "ag-grid-community";
import { useCallback, useState } from "react";
import { useNavigate } from "react-router";

import { api, ApiError, downloadExport, type DocumentRow } from "../api";
import { DocumentsGrid } from "./DocumentsGrid";

type Scope = "batch" | "selection" | "filter";

/** Tableau des documents d'un lot : filtre « À vérifier », vérification, exports. */
export function DocumentsSection({ batchId, rows }: { batchId: string; rows: DocumentRow[] }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [grid, setGrid] = useState<GridApi<DocumentRow> | null>(null);
  const [onlyReview, setOnlyReview] = useState(false);
  const [scope, setScope] = useState<Scope>("batch");
  const [withStatus, setWithStatus] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const toReview = rows.filter((r) => r.status === "to_review");
  const shown = onlyReview ? toReview : rows;

  const onEdit = useCallback(
    async (id: string, field: string, value: string | null) => {
      setError(null);
      try {
        await api.patchFields(id, { [field]: value });
        await queryClient.invalidateQueries({ queryKey: ["documents", batchId] });
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "La correction n'a pas été enregistrée.");
        throw e;
      }
    },
    [batchId, queryClient],
  );

  const onPair = async () => {
    setError(null);
    try {
      await api.runPairing(batchId);
      setInfo("Appariement recto/verso lancé : le tableau se mettra à jour à la fin.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "L'appariement n'a pas pu être lancé.");
    }
  };

  const onExport = async (format: "xlsx" | "csv") => {
    setError(null);
    const columns =
      grid
        ?.getAllDisplayedColumns()
        .map((c) => c.getColDef().field)
        .filter((f): f is string => !!f) ?? undefined;
    let ids: string[] | undefined;
    if (scope === "selection") {
      ids = grid?.getSelectedRows().map((r) => String(r.id)) ?? [];
      if (ids.length === 0) {
        setError("Sélectionnez au moins une ligne à exporter.");
        return;
      }
    } else if (scope === "filter") {
      ids = [];
      grid?.forEachNodeAfterFilterAndSort((n) => n.data && ids!.push(String(n.data.id)));
    } else if (onlyReview) {
      ids = toReview.map((r) => String(r.id));
    }
    try {
      await downloadExport({ format, batch: batchId, ids, columns, include_status: withStatus });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "L'export n'a pas abouti. Réessayez.");
    }
  };

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-medium">Documents</h2>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <button
            type="button"
            onClick={() => setOnlyReview((v) => !v)}
            aria-pressed={onlyReview}
            className={`rounded-md border px-3 py-1.5 ${onlyReview ? "border-amber-600 bg-amber-50 text-amber-900" : "border-slate-300"}`}
          >
            À vérifier ({toReview.length})
          </button>
          <button
            type="button"
            disabled={toReview.length === 0}
            onClick={() => navigate(`/documents/${String(toReview[0]?.id)}`)}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-white hover:bg-slate-700 disabled:opacity-40"
          >
            Vérifier les documents
          </button>
          <button
            type="button"
            onClick={() => void onPair()}
            className="rounded-md border border-slate-300 px-3 py-1.5 hover:bg-slate-100"
          >
            Lancer l'appariement
          </button>
          <span className="mx-1 h-6 w-px bg-slate-200" />
          <label className="flex items-center gap-1">
            Exporter
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value as Scope)}
              className="rounded border border-slate-300 px-1 py-1"
            >
              <option value="batch">le lot{onlyReview ? " (à vérifier)" : ""}</option>
              <option value="filter">le filtre courant</option>
              <option value="selection">la sélection</option>
            </select>
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={withStatus} onChange={(e) => setWithStatus(e.target.checked)} />
            statut et alertes
          </label>
          <button
            type="button"
            onClick={() => void onExport("xlsx")}
            className="rounded-md border border-slate-300 px-3 py-1.5 hover:bg-slate-100"
          >
            Exporter en XLSX
          </button>
          <button
            type="button"
            onClick={() => void onExport("csv")}
            className="rounded-md border border-slate-300 px-3 py-1.5 hover:bg-slate-100"
          >
            Exporter en CSV
          </button>
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      {info && !error && (
        <p role="status" className="text-sm text-slate-600">
          {info}
        </p>
      )}
      <DocumentsGrid rows={shown} onEdit={onEdit} onReady={setGrid} />
      <p className="text-xs text-slate-500">
        Double-cliquez sur une cellule pour la corriger (le document passe en « Validé (manuel) »). Cliquez sur le
        statut pour ouvrir l'écran de vérification.
      </p>
    </section>
  );
}
