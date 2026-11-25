import type { ReactNode } from "react";
import { Link } from "react-router";

import { useAuth } from "../auth";

export function Layout({ children }: { children: ReactNode }) {
  const { me, signOut } = useAuth();
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
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
