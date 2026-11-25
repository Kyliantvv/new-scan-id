import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import { api, ApiError, uploadFile } from "../api";
import { DocumentsSection } from "../components/DocumentsSection";
import { DropZone } from "../components/DropZone";
import { FileList, type FileItem } from "../components/FileList";
import { viewFromUpload, type FileView } from "../format";
import { useBatchEvents } from "../useBatchEvents";

const PARALLEL_UPLOADS = 3;

interface PendingUpload {
  key: string;
  name: string;
  size: number;
  view: FileView;
  fileId: string | null;
}

export function BatchPage() {
  const { batchId = "" } = useParams();
  const queryClient = useQueryClient();
  const batch = useQuery({ queryKey: ["batch", batchId], queryFn: () => api.getBatch(batchId) });
  const documents = useQuery({
    queryKey: ["documents", batchId],
    queryFn: () => api.listDocuments(batchId),
  });
  const { jobsByFile, connected } = useBatchEvents(batchId);
  const [pending, setPending] = useState<PendingUpload[]>([]);

  const update = useCallback((key: string, patch: Partial<PendingUpload>) => {
    setPending((prev) => prev.map((p) => (p.key === key ? { ...p, ...patch } : p)));
  }, []);

  const onFiles = useCallback(
    (files: File[]) => {
      const items = files.map((f, i) => ({
        key: `${Date.now()}-${i}-${f.name}`,
        name: f.name,
        size: f.size,
        view: { kind: "uploading", fraction: 0 } as FileView,
        fileId: null,
        file: f,
      }));
      setPending((prev) => [...items.map(({ file: _f, ...rest }) => rest), ...prev]);
      const queue = [...items];
      const worker = async () => {
        for (let item = queue.shift(); item; item = queue.shift()) {
          const { key, file } = item;
          try {
            const result = await uploadFile(batchId, file, (fraction) =>
              update(key, { view: { kind: "uploading", fraction } }),
            );
            update(key, { view: viewFromUpload(result), fileId: result.file_id });
          } catch (e) {
            update(key, { view: { kind: "rejected", error: e instanceof ApiError ? e.code : "NETWORK" } });
          }
        }
      };
      void Promise.all(Array.from({ length: Math.min(PARALLEL_UPLOADS, items.length) }, worker)).then(() =>
        queryClient.invalidateQueries({ queryKey: ["batch", batchId] }),
      );
    },
    [batchId, queryClient, update],
  );

  // Liste affichée : envois en cours ou refusés, puis fichiers du lot (état du job en direct).
  const items: FileItem[] = useMemo(() => {
    const serverFiles = batch.data?.files ?? [];
    const known = new Set(serverFiles.map((f) => f.id));
    const local = pending
      .filter((p) => !p.fileId || !known.has(p.fileId))
      .map((p) => {
        const live = p.fileId ? jobsByFile[p.fileId] : undefined;
        return { key: p.key, name: p.name, size: p.size, view: live ? { kind: "job" as const, job: live } : p.view };
      });
    const remote = [...serverFiles].reverse().map((f) => {
      const job = jobsByFile[f.id] ?? f.job;
      const view: FileView = f.duplicate_of_id
        ? { kind: "duplicate" }
        : job
          ? { kind: "job", job }
          : { kind: "uploading", fraction: 1 };
      return { key: f.id, name: f.original_name, size: f.size_bytes, view };
    });
    return [...local, ...remote];
  }, [batch.data, pending, jobsByFile]);

  if (batch.isError) {
    const notFound = batch.error instanceof ApiError && batch.error.status === 404;
    return (
      <p role="alert" className="text-red-700">
        {notFound ? "Ce lot n'existe pas ou a été purgé." : "Impossible de charger le lot. Rechargez la page."}{" "}
        <Link to="/" className="underline">
          Revenir aux lots
        </Link>
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold">
          <Link to="/" className="text-slate-500 hover:underline">
            Lots
          </Link>{" "}
          / {batch.data?.name ?? ""}
        </h1>
        <span className={`text-sm ${connected ? "text-emerald-700" : "text-slate-500"}`}>
          {connected ? "Progression en direct" : "Connexion au suivi…"}
        </span>
      </div>
      <DropZone onFiles={onFiles} />
      <FileList items={items} />
      <DocumentsSection batchId={batchId} rows={documents.data ?? []} />
    </div>
  );
}
