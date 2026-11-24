import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";

import { api, ApiError } from "../api";
import { formatDateTime } from "../format";

export function BatchesPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const batches = useQuery({ queryKey: ["batches"], queryFn: api.listBatches });
  const create = useMutation({
    mutationFn: (n: string) => api.createBatch(n),
    onSuccess: (batch) => {
      void queryClient.invalidateQueries({ queryKey: ["batches"] });
      void navigate(`/lots/${batch.id}`);
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (name.trim()) create.mutate(name.trim());
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-xl font-semibold">Lots</h1>
        <form onSubmit={onSubmit} className="flex items-end gap-2">
          <div className="space-y-1">
            <label htmlFor="batch-name" className="block text-sm font-medium">
              Nom du nouveau lot
            </label>
            <input
              id="batch-name"
              value={name}
              maxLength={200}
              onChange={(e) => setName(e.target.value)}
              placeholder="ex. Salon X — novembre"
              className="w-72 rounded-md border border-slate-300 px-3 py-2"
            />
          </div>
          <button
            type="submit"
            disabled={create.isPending}
            className="rounded-md bg-blue-700 px-3 py-2 font-medium text-white hover:bg-blue-800 disabled:opacity-60"
          >
            Créer le lot
          </button>
        </form>
      </div>
      {create.error && (
        <p role="alert" className="text-sm text-red-700">
          {create.error instanceof ApiError ? create.error.message : "Création impossible."}
        </p>
      )}

      {batches.isError && (
        <p role="alert" className="text-sm text-red-700">
          Impossible de charger les lots. Rechargez la page.
        </p>
      )}
      {batches.data && batches.data.length === 0 && (
        <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center text-slate-600">
          Aucun lot pour l'instant. Donnez un nom au premier lot ci-dessus pour commencer.
        </div>
      )}
      {batches.data && batches.data.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-slate-600">
              <tr>
                <th className="px-4 py-2 font-medium">Lot</th>
                <th className="px-4 py-2 font-medium">Créé le</th>
                <th className="px-4 py-2 font-medium">Fichiers</th>
                <th className="px-4 py-2 font-medium">Analyse</th>
              </tr>
            </thead>
            <tbody>
              {batches.data.map((b) => {
                const pending = (b.job_counts.queued ?? 0) + (b.job_counts.running ?? 0);
                const failed = b.job_counts.failed ?? 0;
                return (
                  <tr key={b.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                    <td className="px-4 py-2">
                      <Link to={`/lots/${b.id}`} className="font-medium text-blue-800 hover:underline">
                        {b.name}
                      </Link>
                    </td>
                    <td className="px-4 py-2 tabular-nums">{formatDateTime(b.created_at)}</td>
                    <td className="px-4 py-2 tabular-nums">{b.file_count}</td>
                    <td className="px-4 py-2">
                      {pending > 0 ? `${pending} en cours` : "À jour"}
                      {failed > 0 ? ` · ${failed} en échec` : ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
