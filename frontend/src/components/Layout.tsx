import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { api } from "../api";
import { useAuth } from "../auth";

export function Layout({ children }: { children: ReactNode }) {
  const { me, signOut } = useAuth();
  // Alerte « macOS a changé depuis le dernier banc » : pour les administrateurs, qui lancent le banc.
  const health = useQuery({
    queryKey: ["admin", "health"],
    queryFn: api.health,
    enabled: me?.role === "admin",
    staleTime: 10 * 60_000,
  });
  const alert = health.data?.macos_alert;
  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-4 px-4">
          <Link to="/" className="font-semibold tracking-tight">
            IDReader
          </Link>
          <div className="flex items-center gap-3 text-sm">
            {me?.role === "admin" && (
              <Link to="/admin" className="text-slate-700 hover:underline">
                Administration
              </Link>
            )}
            <span className="hidden text-slate-600 sm:inline">
              {me?.email}
              {me?.role === "admin" ? " · admin" : ""}
            </span>
            <button
              type="button"
              onClick={() => void signOut()}
              className="rounded-md border border-slate-300 px-3 py-1.5 hover:bg-slate-100"
            >
              Se déconnecter
            </button>
          </div>
        </div>
      </header>
      {alert?.changed && (
        <div role="alert" className="border-b border-amber-300 bg-amber-50">
          <p className="mx-auto max-w-7xl px-4 py-2 text-sm text-amber-900">
            {alert.message} <code className="rounded bg-amber-100 px-1">uv run idreader bench --set golden --compare last</code>
          </p>
        </div>
      )}
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
