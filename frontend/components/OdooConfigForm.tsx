"use client";

// Odoo configuration: only URL, database name, and API key; plus the Disconnect button.
//
// Purpose: the admin connects the application to a single Odoo project from the UI. Two states:
// - Connected (configuration saved): summary of URL and database + Disconnect button (with confirmation).
// - Not connected: URL, database, API key form. After Disconnect, this form is used for another Odoo project.
// The API key is WRITE-ONLY: the field is always empty, the backend never sends it back, and the value
// is removed from state after submission. Credentials in `.env` are no longer used.
// If the backend rejects the request because this is a different Odoo and local data belonging to the previous Odoo still exists
// (ODOO_SWITCH_REQUIRES_RESET), the user is asked to approve a local data reset, after which data is repopulated from the new Odoo.
// Localhost/internal network addresses are permitted only if the server enables ODOO_ALLOW_PRIVATE_HOSTS.
//
// Related: uses hooks/useOdooConfig.ts, hooks/useOdooConnection.ts (test after save) and
// hooks/useSyncInventory.ts (repopulation after reset); mounted in app/odoo/page.tsx.
import { useState } from "react";

import { useDisconnectOdoo, useOdooConfig, useSaveOdooConfig } from "@/hooks/useOdooConfig";
import { useTestConnection } from "@/hooks/useOdooConnection";
import { useSyncInventory } from "@/hooks/useSyncInventory";
import { ApiError } from "@/lib/api";
import type { OdooConfig } from "@/lib/types";

const fieldClass = "rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const buttonClass = "rounded-md border border-zinc-300 px-3 py-1.5 text-sm disabled:opacity-60 dark:border-zinc-700";
const primaryClass =
  "rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-900";

function Connect({ config }: { config: OdooConfig }) {
  const [url, setUrl] = useState("");
  const [database, setDatabase] = useState("");
  const [apiKey, setApiKey] = useState("");
  const save = useSaveOdooConfig();
  const test = useTestConnection();
  const sync = useSyncInventory();

  const needsReset = save.error instanceof ApiError && save.error.code === "ODOO_SWITCH_REQUIRES_RESET";

  // Submit the configuration. The API key is held in state only until submission, then cleared.
  function submit(resetLocalData: boolean) {
    save.mutate(
      { url, database, api_key: apiKey, reset_local_data: resetLocalData },
      {
        onSuccess: () => {
          test.mutate();
          if (resetLocalData) sync.mutate(); // repopulate the catalogue and stock from the new Odoo
        },
      },
    );
    if (!needsReset) setApiKey(""); // on a retry after reset confirmation, the key is still required
  }

  return (
    <form
      className="mt-3 flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        submit(false);
      }}
    >
      <p className="text-xs text-zinc-500">
        {config.source === "mock"
          ? "Not connected to Odoo: the built-in mock data is in use. Enter the details of an Odoo project to connect."
          : "Not connected to Odoo. Enter the details of the Odoo project to connect to."}
      </p>
      <label className="flex flex-col gap-1 text-sm">
        Odoo URL
        <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://mycompany.odoo.com" className={fieldClass} required />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Database
        <input type="text" value={database} onChange={(e) => setDatabase(e.target.value)} className={fieldClass} required />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        API key
        <input
          type="password"
          autoComplete="new-password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="Paste the API key"
          className={fieldClass}
          required
        />
        <span className="text-xs text-zinc-500">Write-only: stored encrypted on the server and never shown again.</span>
      </label>
      <div className="flex items-center gap-3">
        <button type="submit" disabled={save.isPending} className={primaryClass}>
          {save.isPending ? "Connecting..." : "Connect and test"}
        </button>
      </div>
      {needsReset && save.error && (
        <div role="alert" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
          <p>{save.error.message}</p>
          <p className="mt-1">Resetting deletes that local data and loads the catalogue and stock of the new Odoo. Odoo itself is not changed.</p>
          <button type="button" onClick={() => submit(true)} disabled={save.isPending || !apiKey} className={`${buttonClass} mt-2`}>
            Reset local data and connect
          </button>
          {!apiKey && <span className="ml-2 text-xs">Re-enter the API key first.</span>}
        </div>
      )}
      {save.isError && !needsReset && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {save.error instanceof ApiError && save.error.code === "ENCRYPTION_NOT_CONFIGURED"
            ? "The server has no encryption key (SETTINGS_ENCRYPTION_KEY), so the API key cannot be stored."
            : save.error.message}
        </p>
      )}
    </form>
  );
}

function Connected({ config }: { config: OdooConfig }) {
  const [confirming, setConfirming] = useState(false);
  const disconnect = useDisconnectOdoo();
  return (
    <div className="mt-3 flex flex-col gap-3">
      <p className="text-xs text-zinc-500">These settings are saved. The connection status above shows whether Odoo accepted them.</p>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs text-zinc-500">Odoo URL</dt>
          <dd className="break-all">{config.url}</dd>
        </div>
        <div>
          <dt className="text-xs text-zinc-500">Database</dt>
          <dd>{config.database}</dd>
        </div>
        <div>
          <dt className="text-xs text-zinc-500">API key</dt>
          <dd>Saved (hidden)</dd>
        </div>
      </dl>
      {!confirming ? (
        <div>
          <button type="button" onClick={() => setConfirming(true)} className={buttonClass}>
            Disconnect
          </button>
        </div>
      ) : (
        <div role="alert" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
          <p>
            Disconnecting deletes the saved URL, database and API key. Stock sync and posting sales to Odoo stop until you connect again.
            Sales already recorded stay queued.
          </p>
          <div className="mt-2 flex gap-2">
            <button type="button" onClick={() => disconnect.mutate()} disabled={disconnect.isPending} className={buttonClass}>
              {disconnect.isPending ? "Disconnecting..." : "Yes, disconnect"}
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={disconnect.isPending} className={buttonClass}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {disconnect.isError && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {disconnect.error.message}
        </p>
      )}
    </div>
  );
}

export default function OdooConfigForm() {
  const config = useOdooConfig();
  return (
    <section aria-labelledby="odoo-config-heading" className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <h2 id="odoo-config-heading" className="text-sm font-semibold">
        Odoo connection settings
      </h2>
      {config.isPending && <p className="mt-3 text-sm text-zinc-500">Loading settings...</p>}
      {config.isError && (
        <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-400">
          {config.error.message}{" "}
          <button type="button" onClick={() => config.refetch()} className="underline">
            Retry
          </button>
        </p>
      )}
      {/* key= creates a new (empty) form each time the configuration changes, e.g. after Disconnect */}
      {config.isSuccess &&
        (config.data.configured ? (
          <Connected key={`c|${config.data.url}|${config.data.database}`} config={config.data} />
        ) : (
          <Connect key="connect" config={config.data} />
        ))}
    </section>
  );
}
