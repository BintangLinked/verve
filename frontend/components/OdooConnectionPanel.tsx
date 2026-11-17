"use client";

// Odoo connection status panel (read-only): Mock/Live mode, database name, status, Odoo version, time of the
// last check, and the "Test connection" button.
//
// Purpose: shows whether the backend is connected to Odoo. The panel does not accept credentials; what the
// Test button sends is only a "re-check" command. This page is displayed only after login (AuthGate), and
// an expired session is handled by QueryProvider.
//
// Related: uses hooks/useOdooConnection.ts; mounted in app/odoo/page.tsx.
import { useOdooConnection, useTestConnection } from "@/hooks/useOdooConnection";
import type { ConnectionState } from "@/lib/types";

const STATE_STYLE: Record<ConnectionState, { label: string; className: string }> = {
  connected: { label: "Connected", className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300" },
  disconnected: { label: "Disconnected", className: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" },
  unknown: { label: "Not checked", className: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300" },
};

const buttonClass = "rounded-md border border-zinc-300 px-3 py-1.5 text-sm disabled:opacity-60 dark:border-zinc-700";

export default function OdooConnectionPanel() {
  const connection = useOdooConnection(true);
  const test = useTestConnection();

  return (
    <section aria-labelledby="odoo-connection-heading" className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="odoo-connection-heading" className="text-sm font-semibold">
          Connection status
        </h2>
        <button type="button" onClick={() => test.mutate()} disabled={test.isPending} className={buttonClass}>
          {test.isPending ? "Testing..." : "Test connection"}
        </button>
      </div>

      {connection.isPending && <p className="mt-3 text-sm text-zinc-500">Loading connection status...</p>}

      {connection.isError && (
        <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-400">
          {connection.error.message}{" "}
          <button type="button" onClick={() => connection.refetch()} className="underline">
            Retry
          </button>
        </p>
      )}

      {connection.isSuccess && (
        <div className="mt-3 flex flex-col gap-2" aria-live="polite">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-800 dark:bg-sky-950 dark:text-sky-300">
              {connection.data.mode === "mock" ? "Mock" : "Live"}
            </span>
            <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATE_STYLE[connection.data.status].className}`}>
              {STATE_STYLE[connection.data.status].label}
            </span>
          </div>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-xs text-zinc-500">Database</dt>
              <dd>{connection.data.database ?? "-"}</dd>
            </div>
            <div>
              <dt className="text-xs text-zinc-500">Odoo version</dt>
              <dd>{connection.data.odoo_version ?? "-"}</dd>
            </div>
            <div>
              <dt className="text-xs text-zinc-500">Last checked</dt>
              <dd>{connection.data.last_checked_at ? new Date(connection.data.last_checked_at).toLocaleString() : "Never"}</dd>
            </div>
          </dl>
          {connection.data.error && (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              {connection.data.error.message} <span className="font-mono text-xs">({connection.data.error.code})</span>
            </p>
          )}
          {test.isError && (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              Test failed: {test.error.message}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
