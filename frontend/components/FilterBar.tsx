"use client";

// Dashboard filter bar: product search, store filter, stock status filter, and the Refresh button.
//
// Purpose: modifies the page's InventoryFilters. The search box is debounced by 300 ms so that
// each keystroke does not trigger a request to the backend. The Refresh button triggers a sync from Odoo
// (see hooks/useSyncInventory.ts) and displays the time of the last update.
//
// Related: controlled by app/page.tsx (the filter state is held there; this component is controlled).
import { useEffect, useState } from "react";

import { LOCATION_LABEL, LOCATION_ORDER, type InventoryFilters, type StockStatus } from "@/lib/types";

interface Props {
  value: InventoryFilters;
  onChange: (next: InventoryFilters) => void;
  onRefresh: () => void;
  refreshing: boolean;
  lastUpdated?: string; // as_of from the backend response
  syncError?: string; // message if the Odoo sync fails
}

const controlClass =
  "rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";

export default function FilterBar({ value, onChange, onRefresh, refreshing, lastUpdated, syncError }: Props) {
  const [search, setSearch] = useState(value.q ?? "");

  // Debounce: send the search change to the parent after the user stops typing.
  useEffect(() => {
    const timer = setTimeout(() => {
      if (search !== (value.q ?? "")) onChange({ ...value, q: search || undefined });
    }, 300);
    return () => clearTimeout(timer);
  }, [search, value, onChange]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search product, variant or SKU"
          aria-label="Search products"
          className={`${controlClass} sm:w-72`}
        />
        <select
          aria-label="Filter by store"
          value={value.store ?? ""}
          onChange={(e) => onChange({ ...value, store: e.target.value || undefined })}
          className={controlClass}
        >
          <option value="">All locations</option>
          {LOCATION_ORDER.map((code) => (
            <option key={code} value={code}>
              {LOCATION_LABEL[code]}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by stock status"
          value={value.status ?? ""}
          onChange={(e) => onChange({ ...value, status: (e.target.value || undefined) as StockStatus | undefined })}
          className={controlClass}
        >
          <option value="">All statuses</option>
          <option value="OK">OK</option>
          <option value="LOW">Low</option>
          <option value="OUT">Out</option>
        </select>
        <div className="flex items-center gap-3 sm:ml-auto">
          {lastUpdated && (
            <span className="text-xs text-zinc-500">Last updated {new Date(lastUpdated).toLocaleTimeString()}</span>
          )}
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-900"
          >
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>
      {syncError && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          Refresh failed: {syncError}. Showing the last known stock.
        </p>
      )}
    </div>
  );
}
