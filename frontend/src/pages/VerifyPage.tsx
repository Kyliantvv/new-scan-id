import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, ApiError, cropImageUrl, type DocumentDetail, type FieldDetail, type MrzDetail } from "../api";
import { useAuth } from "../auth";
import { ZoomImage, type Overlay } from "../components/ZoomImage";
import {
  cycleValue,
  DATE_FIELDS,
  DOC_STATUS,
  DOC_TYPES,
  FIELD_STATUS,
  formatDate,
  nextToReview,
  parseDisplayDate,
  SOURCE_LABELS,
  TONE_CLASSES,
} from "../format";

type Side = "front" | "back";

const shown = (f: FieldDetail, v: string | null) => (v && DATE_FIELDS.has(f.name) ? formatDate(v) : (v ?? ""));
const stored = (name: string, text: string) => (DATE_FIELDS.has(name) ? parseDisplayDate(text) : text.trim());

function initialValues(doc: DocumentDetail): Record<string, string> {
  return Object.fromEntries(doc.fields.map((f) => [f.name, shown(f, f.value)]));
}

/** Écran de vérification (docs/01_SPEC_FONCTIONNELLE.md § Écran de vérification). */
export function VerifyPage() {
  const { documentId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { me } = useAuth();
  const doc = useQuery({ queryKey: ["document", documentId], queryFn: () => api.getDocument(documentId) });
  const batchId = doc.data?.batch_id ?? "";
  const queue = useQuery({
    queryKey: ["documents", batchId, "to_review"],
    queryFn: () => api.listDocuments(batchId, "to_review"),
    enabled: !!batchId,
  });

  const [values, setValues] = useState<Record<string, string>>({});
  const [active, setActive] = useState<string | null>(null);
  const [side, setSide] = useState<Side>("front");
  const [rotation, setRotation] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const inputs = useRef<Record<string, HTMLInputElement | null>>({});

  // Remise à zéro à chaque document ; focus sur le premier champ douteux.
  useEffect(() => {
    const d = doc.data;
    if (!d) return;
    setValues(initialValues(d));
    setSide(d.front ? "front" : "back");
    setRotation(0);
    setMessage(null);
    const first = d.fields.find((f) => !["ok", "repaired", "manual"].includes(f.status)) ?? d.fields[0];
    setActive(first?.name ?? null);
    if (first) requestAnimationFrame(() => inputs.current[first.name]?.focus());
  }, [doc.data]);

  const initial = useMemo(() => (doc.data ? initialValues(doc.data) : {}), [doc.data]);
  const changes = useMemo(() => {
    const out: Record<string, string | null> = {};
    for (const [name, text] of Object.entries(values)) {
      if (text !== initial[name]) out[name] = text.trim() === "" ? null : stored(name, text);
    }
    return out;
  }, [values, initial]);

  const goNext = useCallback(() => {
    const ids = (queue.data ?? []).map((r) => String(r.id));
    const next = nextToReview(ids, documentId);
    void queryClient.invalidateQueries({ queryKey: ["documents", batchId] });
    navigate(next ? `/documents/${next}` : `/lots/${batchId}`);
  }, [queue.data, documentId, queryClient, batchId, navigate]);

  const onError = (e: unknown) => setMessage(e instanceof ApiError ? e.message : "L'action n'a pas abouti. Réessayez.");
  const validate = useMutation({ mutationFn: () => api.validate(documentId, changes), onSuccess: goNext, onError });
  const reject = useMutation({ mutationFn: () => api.reject(documentId), onSuccess: goNext, onError });
  const reprocess = useMutation({
    mutationFn: () => api.reprocess(documentId),
    onSuccess: () => navigate(`/lots/${batchId}`),
    onError,
  });
  const remove = useMutation({ mutationFn: () => api.deleteDocument(documentId), onSuccess: goNext, onError });
  const golden = useMutation({
    mutationFn: () => api.addToGolden(documentId),
    onSuccess: () => setMessage("Document ajouté au jeu de test."),
    onError,
  });
  const [pickerOpen, setPickerOpen] = useState(false);
  const afterPairing = (id: string | undefined) => {
    setPickerOpen(false);
    void queryClient.invalidateQueries({ queryKey: ["documents", batchId] });
    if (id) navigate(`/documents/${id}`);
  };
  const pair = useMutation({
    mutationFn: (otherId: string) => api.pairDocuments(documentId, otherId),
    onSuccess: (doc) => afterPairing(doc.id),
    onError,
  });
  const unpair = useMutation({
    mutationFn: () => api.unpair(documentId),
    onSuccess: (ids) => afterPairing(ids[0]),
    onError,
  });
  // Autres documents d'une seule face du lot, chargés à l'ouverture du choix manuel.
  const singles = useQuery({
    queryKey: ["documents", batchId, "all"],
    queryFn: () => api.listDocuments(batchId),
    enabled: !!batchId && pickerOpen,
  });
  const pairCandidates = (singles.data ?? []).filter(
    (r) => String(r.id) !== documentId && !(r.front_crop_id && r.back_crop_id),
  );

  const d = doc.data;
  const crop = d ? (side === "front" ? d.front : d.back) : null;
  const overlays: Overlay[] = useMemo(
    () =>
      (d?.fields ?? [])
        .filter((f) => f.bbox && (f.side ?? "front") === side)
        .map((f) => ({
          key: f.name,
          box: f.bbox!,
          active: f.name === active,
          tone: f.status === "conflict" ? "error" : ["ok", "repaired", "manual"].includes(f.status) ? "ok" : "warn",
        })),
    [d, side, active],
  );

  const onPageKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const typing = e.target instanceof HTMLInputElement;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!validate.isPending) validate.mutate();
    } else if (!typing && (e.key === "r" || e.key === "R")) {
      setRotation((r) => (r + 90) % 360);
    } else if (!typing && (e.key === "v" || e.key === "V") && d?.front && d.back) {
      setSide((s) => (s === "front" ? "back" : "front"));
    } else if (!typing && (e.key === "p" || e.key === "P") && d && !d.paired) {
      // P : associer à la face suggérée, sinon ouvrir le choix de l'autre face.
      if (d.suggestion) pair.mutate(d.suggestion.id);
      else setPickerOpen(true);
    }
  };

  const onFieldKey = (f: FieldDetail, e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const options = [shown(f, f.value), ...f.candidates.map((c) => shown(f, c))];
      setValues((v) => ({ ...v, [f.name]: cycleValue(v[f.name] ?? "", options, e.key === "ArrowDown" ? 1 : -1) }));
    } else if (e.key === "Escape") {
      e.preventDefault();
      setValues((v) => ({ ...v, [f.name]: initial[f.name] ?? "" }));
    }
  };

  if (doc.isError) {
    return (
      <p role="alert" className="text-red-700">
        Ce document est introuvable ou a été supprimé.{" "}
        <Link to="/" className="underline">
          Revenir aux lots
        </Link>
      </p>
    );
  }
  if (!d) return null;

  const status = DOC_STATUS[d.status] ?? { label: d.status, tone: "neutral" as const };
  const pending =
    validate.isPending || reject.isPending || remove.isPending || reprocess.isPending || pair.isPending || unpair.isPending;
  const remaining = (queue.data ?? []).filter((r) => String(r.id) !== d.id).length;

  return (
    <div className="space-y-4" onKeyDown={onPageKey}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">
          <Link to={`/lots/${d.batch_id}`} className="text-slate-500 hover:underline">
            Lot
          </Link>{" "}
          / {DOC_TYPES[d.doc_type ?? ""] ?? "Document"} {d.issuing_state ?? ""}
          <span className={`ml-3 rounded px-2 py-0.5 align-middle text-sm ${TONE_CLASSES[status.tone]}`}>
            {status.label}
          </span>
        </h1>
        <span className="text-sm text-slate-600">
          {remaining > 0 ? `${remaining} autre(s) document(s) à vérifier dans ce lot` : "Dernier document à vérifier"}
        </span>
      </div>

      {(d.reasons.length > 0 || d.alert_labels.length > 0) && (
        <ul className="flex flex-wrap gap-2 text-sm">
          {d.reasons.map((r, i) => (
            <li key={`r${i}`} className="rounded bg-amber-50 px-2 py-1 text-amber-900">
              {r.label}
              {r.field ? ` — ${d.fields.find((f) => f.name === r.field)?.label ?? r.field}` : ""}
            </li>
          ))}
          {d.alert_labels.map((a) => (
            <li key={a} className="rounded bg-red-50 px-2 py-1 text-red-800">
              {a}
            </li>
          ))}
        </ul>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(22rem,2fr)]">
        <section className="flex min-h-[28rem] flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {(["front", "back"] as const).map((s) => (
              <button
                key={s}
                type="button"
                disabled={!(s === "front" ? d.front : d.back)}
                onClick={() => setSide(s)}
                className={`rounded-md border px-3 py-1 disabled:opacity-40 ${side === s ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300"}`}
              >
                {s === "front" ? "Recto" : "Verso"}
              </button>
            ))}
            <button
              type="button"
              title="Rotation (R)"
              onClick={() => setRotation((r) => (r + 90) % 360)}
              className="rounded-md border border-slate-300 px-3 py-1"
            >
              Tourner <kbd className="text-xs text-slate-500">R</kbd>
            </button>
            {crop && (
              <span className="text-slate-500">
                {crop.file_name} · page {crop.page_index + 1}
              </span>
            )}
          </div>
          <div className="h-[32rem] lg:h-auto lg:flex-1">
            {crop ? (
              <ZoomImage
                src={cropImageUrl(crop.id)}
                width={crop.width}
                height={crop.height}
                rotation={rotation}
                overlays={overlays}
                label={side === "front" ? "Recto du document" : "Verso du document"}
              />
            ) : (
              <p className="text-slate-600">Aucune image pour cette face.</p>
            )}
          </div>
          {d.mrz && <MrzView mrz={d.mrz} />}
        </section>

        <section className="space-y-3">
          <form
            className="space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              validate.mutate();
            }}
          >
            {d.fields.map((f) => (
              <FieldRow
                key={f.name}
                field={f}
                value={values[f.name] ?? ""}
                changed={(values[f.name] ?? "") !== (initial[f.name] ?? "")}
                active={active === f.name}
                inputRef={(el) => {
                  inputs.current[f.name] = el;
                }}
                onFocus={() => {
                  setActive(f.name);
                  if (f.side && f.bbox && (f.side === "front" ? d.front : d.back)) setSide(f.side);
                }}
                onChange={(text) => setValues((v) => ({ ...v, [f.name]: text }))}
                onKeyDown={(e) => onFieldKey(f, e)}
              />
            ))}
          </form>

          {message && (
            <p role="status" className="rounded bg-slate-100 px-3 py-2 text-sm">
              {message}
            </p>
          )}

          <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-3">
            <button
              type="button"
              disabled={pending}
              onClick={() => validate.mutate()}
              className="rounded-md bg-emerald-700 px-4 py-2 font-medium text-white hover:bg-emerald-800 disabled:opacity-60"
            >
              {Object.keys(changes).length ? "Corriger et valider" : "Valider"}{" "}
              <kbd className="text-xs opacity-80">Entrée</kbd>
            </button>
            <button
              type="button"
              disabled={pending}
              onClick={() => reject.mutate()}
              className="rounded-md border border-slate-300 px-3 py-2 hover:bg-slate-100"
            >
              Rejeter : pas une pièce d'identité
            </button>
            <button
              type="button"
              disabled={pending}
              onClick={() => reprocess.mutate()}
              className="rounded-md border border-slate-300 px-3 py-2 hover:bg-slate-100"
            >
              Relancer l'analyse
            </button>
            {d.paired ? (
              <button
                type="button"
                disabled={pending}
                onClick={() => unpair.mutate()}
                className="rounded-md border border-slate-300 px-3 py-2 hover:bg-slate-100"
              >
                Dissocier recto et verso
              </button>
            ) : (
              <button
                type="button"
                disabled={pending}
                onClick={() => setPickerOpen((o) => !o)}
                aria-expanded={pickerOpen}
                className="rounded-md border border-slate-300 px-3 py-2 hover:bg-slate-100"
              >
                Associer à une autre face
              </button>
            )}
            {me?.role === "admin" && d.golden_enabled && (
              <button
                type="button"
                disabled={pending || golden.isPending}
                onClick={() => golden.mutate()}
                className="rounded-md border border-slate-300 px-3 py-2 hover:bg-slate-100"
              >
                Ajouter au jeu de test
              </button>
            )}
            <button
              type="button"
              disabled={pending}
              onClick={() => {
                if (window.confirm("Supprimer ce document et ses images ? Cette action est définitive.")) remove.mutate();
              }}
              className="rounded-md border border-red-300 px-3 py-2 text-red-700 hover:bg-red-50"
            >
              Supprimer le document
            </button>
          </div>
          {!d.paired && d.suggestion && (
            <div className="flex flex-wrap items-center gap-3 rounded-md bg-sky-50 p-3 text-sm text-sky-900">
              {d.suggestion.crop_id && (
                <img
                  src={cropImageUrl(d.suggestion.crop_id)}
                  alt="Autre face suggérée"
                  className="h-16 w-auto rounded border border-sky-200"
                />
              )}
              <span>
                Autre face probable : {d.suggestion.file_name ?? "fichier du lot"}
                {d.suggestion.score != null ? ` (score ${d.suggestion.score.toFixed(2)})` : ""}
              </span>
              <button
                type="button"
                disabled={pending}
                onClick={() => pair.mutate(d.suggestion!.id)}
                className="rounded-md bg-sky-700 px-3 py-1.5 text-white hover:bg-sky-800"
              >
                Associer ces deux faces (P)
              </button>
            </div>
          )}
          {pickerOpen && !d.paired && (
            <div className="space-y-2 rounded-md border border-slate-200 p-3 text-sm">
              <p className="font-medium">Choisir l'autre face de ce document</p>
              {singles.isLoading && <p className="text-slate-500">Chargement…</p>}
              {singles.isSuccess && pairCandidates.length === 0 && (
                <p className="text-slate-500">Aucun autre document d'une seule face dans ce lot.</p>
              )}
              <ul className="max-h-64 space-y-1 overflow-y-auto">
                {pairCandidates.map((r) => (
                  <li key={String(r.id)}>
                    <button
                      type="button"
                      disabled={pending}
                      onClick={() => pair.mutate(String(r.id))}
                      className="flex w-full items-center gap-3 rounded px-2 py-1 text-left hover:bg-slate-100"
                    >
                      {typeof r.preview_crop_id === "string" && (
                        <img src={cropImageUrl(r.preview_crop_id)} alt="" className="h-10 w-auto rounded" />
                      )}
                      <span>
                        {String(r.source_file ?? "Fichier")}
                        {typeof r.page_index === "number" ? `, page ${r.page_index + 1}` : ""}
                        {r.surname ? ` · ${String(r.surname)}` : ""}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-xs text-slate-500">
            Tab / Maj+Tab : champ suivant ou précédent · ↑ / ↓ : alternative · Échap : annuler la saisie du champ ·
            Entrée : valider et passer au suivant · R : rotation · V : recto/verso · P : associer recto/verso
          </p>
        </section>
      </div>
    </div>
  );
}

function FieldRow({
  field: f,
  value,
  changed,
  active,
  inputRef,
  onFocus,
  onChange,
  onKeyDown,
}: {
  field: FieldDetail;
  value: string;
  changed: boolean;
  active: boolean;
  inputRef: (el: HTMLInputElement | null) => void;
  onFocus: () => void;
  onChange: (text: string) => void;
  onKeyDown: (e: KeyboardEvent<HTMLInputElement>) => void;
}) {
  const st = changed ? FIELD_STATUS.manual! : (FIELD_STATUS[f.status] ?? FIELD_STATUS.missing!);
  const id = `field-${f.name}`;
  const options = f.candidates.filter((c) => c !== f.value);
  return (
    <div className={`rounded-md border px-3 py-2 ${active ? "border-slate-900" : "border-slate-200"}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-sm font-medium">
          {f.label}
        </label>
        <span className="flex flex-wrap items-center gap-1 text-xs">
          {(changed ? ["human"] : f.sources).map((s) => (
            <span key={s} className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">
              {SOURCE_LABELS[s] ?? s}
            </span>
          ))}
          <span className={`rounded px-1.5 py-0.5 ${TONE_CLASSES[st.tone]}`}>{st.label}</span>
          {!changed && f.confidence != null && f.status !== "missing" && (
            <span className="text-slate-500">{Math.round(f.confidence * 100)} %</span>
          )}
        </span>
      </div>
      <input
        id={id}
        ref={inputRef}
        value={value}
        onFocus={onFocus}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        list={options.length ? `${id}-alt` : undefined}
        placeholder={DATE_FIELDS.has(f.name) ? "JJ/MM/AAAA" : undefined}
        autoComplete="off"
        spellCheck={false}
        className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-medium uppercase focus:outline-2 focus:outline-slate-900"
      />
      {options.length > 0 && (
        <datalist id={`${id}-alt`}>
          {options.map((o) => (
            <option key={o} value={shown(f, o)} />
          ))}
        </datalist>
      )}
      {f.value_native && <p className="mt-1 text-sm text-slate-600">Écriture d'origine : {f.value_native}</p>}
      {!changed && f.reasons.length > 0 && <p className="mt-1 text-xs text-amber-800">{f.reasons.join(" · ")}</p>}
    </div>
  );
}

/** MRZ brute : chiffres de contrôle en vert ou rouge, caractères réparés soulignés. */
function MrzView({ mrz }: { mrz: MrzDetail }) {
  const checks = new Map(mrz.checks.map((c) => [`${c.line}:${c.index}`, c.ok]));
  const changed = new Set(mrz.changed.map((c) => `${c.line}:${c.index}`));
  return (
    <figure className="overflow-x-auto rounded-md bg-slate-50 p-3">
      <figcaption className="mb-1 text-xs text-slate-500">
        MRZ {mrz.format} ({mrz.side === "front" ? "recto" : "verso"}) · chiffres de contrôle en vert (justes) ou en
        rouge (faux) · caractères réparés soulignés
      </figcaption>
      <pre className="font-mono text-sm leading-6 tracking-wider">
        {mrz.lines.map((line, li) => (
          <div key={li}>
            {[...line].map((ch, ci) => {
              const ok = checks.get(`${li}:${ci}`);
              const cls = [
                ok === true ? "bg-emerald-200 text-emerald-900" : ok === false ? "bg-red-200 text-red-900" : "",
                changed.has(`${li}:${ci}`) ? "underline decoration-2 decoration-amber-600" : "",
              ].join(" ");
              return (
                <span key={ci} className={cls}>
                  {ch}
                </span>
              );
            })}
          </div>
        ))}
      </pre>
    </figure>
  );
}
