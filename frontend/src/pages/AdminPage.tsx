import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import { api, ApiError, type AppSettings, type Role } from "../api";
import { DOC_STATUS, DOC_TYPES } from "../format";

const FIELD_LABELS: Record<string, string> = {
  surname: "Nom",
  given_names: "Prénoms",
  usage_name: "Nom d'usage",
  sex: "Sexe",
  date_of_birth: "Date de naissance",
  place_of_birth: "Lieu de naissance",
  nationality: "Nationalité",
  document_number: "N° du document",
  date_of_issue: "Date de délivrance",
  date_of_expiry: "Date d'expiration",
  authority: "Autorité",
  personal_number: "N° personnel",
};

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : "L'action n'a pas abouti. Réessayez.");

/** Administration : statistiques, réglages, comptes (docs/01_SPEC_FONCTIONNELLE.md § Administration). */
export function AdminPage() {
  return (
    <div className="space-y-8">
      <h1 className="text-xl font-semibold">Administration</h1>
      <StatsSection />
      <SettingsSection />
      <UsersSection />
    </div>
  );
}

function Table({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  if (rows.length === 0) return <p className="text-sm text-slate-500">Aucune donnée pour l'instant.</p>;
  return (
    <table className="w-full max-w-xl text-sm">
      <thead>
        <tr className="border-b border-slate-200 text-left text-slate-600">
          {head.map((h) => (
            <th key={h} className="py-1 pr-4 font-medium">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-slate-100">
            {r.map((c, j) => (
              <td key={j} className="py-1 pr-4">
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StatsSection() {
  const stats = useQuery({ queryKey: ["admin", "stats"], queryFn: () => api.stats() });
  const s = stats.data;
  return (
    <section className="space-y-4">
      <h2 className="text-lg font-medium">Statistiques</h2>
      {stats.isError && <p className="text-red-700">{errorText(stats.error)}</p>}
      {s && (
        <>
          <dl className="grid max-w-3xl grid-cols-2 gap-4 sm:grid-cols-4">
            {[
              ["Documents", String(s.documents)],
              ["Auto-validation", s.automation_pct == null ? "—" : `${s.automation_pct} %`],
              ["Temps moyen", s.avg_ms_per_document == null ? "—" : `${(s.avg_ms_per_document / 1000).toFixed(1)} s`],
              ["À vérifier", String(s.by_status.to_review ?? 0)],
            ].map(([k, v]) => (
              <div key={k} className="rounded-lg border border-slate-200 p-3">
                <dt className="text-sm text-slate-600">{k}</dt>
                <dd className="text-2xl font-semibold tabular-nums">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="grid gap-6 lg:grid-cols-2">
            <div>
              <h3 className="mb-1 font-medium">Par statut</h3>
              <Table
                head={["Statut", "Documents"]}
                rows={Object.entries(s.by_status).map(([k, n]) => [DOC_STATUS[k]?.label ?? k, n])}
              />
            </div>
            <div>
              <h3 className="mb-1 font-medium">Échecs par raison</h3>
              <Table head={["Raison", "Documents"]} rows={Object.entries(s.failures_by_reason)} />
            </div>
            <div>
              <h3 className="mb-1 font-medium">Par pays émetteur</h3>
              <Table head={["Pays", "Documents"]} rows={Object.entries(s.by_country)} />
            </div>
            <div>
              <h3 className="mb-1 font-medium">Par type</h3>
              <Table
                head={["Type", "Documents"]}
                rows={Object.entries(s.by_type).map(([k, n]) => [DOC_TYPES[k] ?? k, n])}
              />
            </div>
            <div>
              <h3 className="mb-1 font-medium">Champs les plus corrigés</h3>
              <Table
                head={["Champ", "Corrections"]}
                rows={s.most_corrected_fields.map(([k, n]) => [FIELD_LABELS[k] ?? k, n])}
              />
            </div>
            <div>
              <h3 className="mb-1 font-medium">Confusions MRZ apprises des corrections</h3>
              <Table
                head={["Lu", "Corrigé en", "Fois"]}
                rows={s.mrz_confusions.map(([a, b, n]) => [a, b, n])}
              />
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function SettingsSection() {
  const queryClient = useQueryClient();
  const current = useQuery({ queryKey: ["admin", "settings"], queryFn: api.getSettings });
  const [form, setForm] = useState<AppSettings | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    if (current.data) setForm(current.data);
  }, [current.data]);
  const save = useMutation({
    mutationFn: (s: AppSettings) => api.putSettings(s),
    onSuccess: (s) => {
      queryClient.setQueryData(["admin", "settings"], s);
      setMessage("Réglages enregistrés.");
    },
    onError: (e) => setMessage(errorText(e)),
  });
  if (!form) return null;
  const num = (k: keyof AppSettings, label: string, step: string, hint: string) => (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      <input
        type="number"
        step={step}
        value={String(form[k])}
        onChange={(e) => setForm({ ...form, [k]: Number(e.target.value) })}
        className="mt-1 block w-40 rounded border border-slate-300 px-2 py-1"
      />
      <span className="text-xs text-slate-500">{hint}</span>
    </label>
  );
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-medium">Réglages</h2>
      <form
        className="grid max-w-3xl gap-4 sm:grid-cols-2"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          setMessage(null);
          save.mutate(form);
        }}
      >
        {num("auto_validate_threshold", "Seuil d'auto-validation", "0.01", "Entre 0,80 et 1. Plus haut : moins d'automatisation, plus de sûreté.")}
        {num("retention_images_days", "Conservation des images (jours)", "1", "Purge automatique des fichiers et recadrages.")}
        {num("retention_data_days", "Conservation des données (jours)", "1", "Purge des lignes du tableau.")}
        <label className="flex items-start gap-2 sm:col-span-2">
          <input
            type="checkbox"
            checked={form.golden_enabled}
            onChange={(e) => setForm({ ...form, golden_enabled: e.target.checked })}
            className="mt-1"
          />
          <span>
            <span className="text-sm font-medium">Bouton « Ajouter au jeu de test »</span>
            <span className="block text-xs text-slate-500">
              Copie le recadrage et les valeurs corrigées dans data/golden/ (disque chiffré). Désactivé par défaut.
            </span>
          </span>
        </label>
        <div className="sm:col-span-2">
          <button
            type="submit"
            disabled={save.isPending}
            className="rounded-md bg-slate-900 px-4 py-2 text-white hover:bg-slate-700 disabled:opacity-60"
          >
            Enregistrer les réglages
          </button>
          {message && (
            <span role="status" className="ml-3 text-sm">
              {message}
            </span>
          )}
        </div>
      </form>
    </section>
  );
}

function UsersSection() {
  const queryClient = useQueryClient();
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: api.listUsers });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("operator");
  const [message, setMessage] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
  const create = useMutation({
    mutationFn: () => api.createUser(email.trim(), password, role),
    onSuccess: () => {
      setEmail("");
      setPassword("");
      setMessage("Compte créé.");
      void refresh();
    },
    onError: (e) => setMessage(errorText(e)),
  });
  const update = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Parameters<typeof api.updateUser>[1] }) =>
      api.updateUser(id, patch),
    onSuccess: () => void refresh(),
    onError: (e) => setMessage(errorText(e)),
  });
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-medium">Comptes</h2>
      <table className="w-full max-w-3xl text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-slate-600">
            <th className="py-1 pr-4 font-medium">Adresse</th>
            <th className="py-1 pr-4 font-medium">Rôle</th>
            <th className="py-1 pr-4 font-medium">État</th>
            <th className="py-1 font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {(users.data ?? []).map((u) => (
            <tr key={u.id} className="border-b border-slate-100">
              <td className="py-1 pr-4">{u.email}</td>
              <td className="py-1 pr-4">
                <select
                  aria-label={`Rôle de ${u.email}`}
                  value={u.role}
                  onChange={(e) => update.mutate({ id: u.id, patch: { role: e.target.value as Role } })}
                  className="rounded border border-slate-300 px-1 py-0.5"
                >
                  <option value="operator">Opérateur</option>
                  <option value="admin">Administrateur</option>
                </select>
              </td>
              <td className="py-1 pr-4">{u.is_active ? "Actif" : "Désactivé"}</td>
              <td className="flex gap-2 py-1">
                <button
                  type="button"
                  onClick={() => update.mutate({ id: u.id, patch: { is_active: !u.is_active } })}
                  className="rounded border border-slate-300 px-2 py-0.5 hover:bg-slate-100"
                >
                  {u.is_active ? "Désactiver" : "Réactiver"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const pw = window.prompt(`Nouveau mot de passe pour ${u.email} (12 caractères minimum)`);
                    if (pw) update.mutate({ id: u.id, patch: { password: pw } });
                  }}
                  className="rounded border border-slate-300 px-2 py-0.5 hover:bg-slate-100"
                >
                  Réinitialiser le mot de passe
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form
        className="flex max-w-3xl flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setMessage(null);
          create.mutate();
        }}
      >
        <label className="block">
          <span className="text-sm font-medium">Adresse e-mail</span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 block rounded border border-slate-300 px-2 py-1"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium">Mot de passe initial</span>
          <input
            type="password"
            required
            minLength={12}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 block rounded border border-slate-300 px-2 py-1"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium">Rôle</span>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            className="mt-1 block rounded border border-slate-300 px-2 py-1"
          >
            <option value="operator">Opérateur</option>
            <option value="admin">Administrateur</option>
          </select>
        </label>
        <button
          type="submit"
          disabled={create.isPending}
          className="rounded-md bg-slate-900 px-4 py-2 text-white hover:bg-slate-700 disabled:opacity-60"
        >
          Créer le compte
        </button>
      </form>
      {message && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
    </section>
  );
}
