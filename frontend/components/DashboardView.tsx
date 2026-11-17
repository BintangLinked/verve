"use client";

// Content of the Verve Lifestyle inventory dashboard (rendered by app/page.tsx).
//
// Purpose: displays stock per location with search and filters, a detail drawer, and clear
// loading / empty / error / refresh states. The filter state and the selected variant are held here;
// child components are "controlled".
//
// Displayed only after login (components/AuthGate.tsx). Odoo status and configuration are at /odoo.
//
// Related: data from hooks/useInventory.ts; the Refresh button uses hooks/useSyncInventory.ts;
// composes FilterBar, InventoryTable,
// VariantDrawer (containing the record-sale form), and ReplenishmentPanel (list of store replenishment needs).
import { useState } from "react";

import FilterBar from "@/components/FilterBar";
import InventoryTable from "@/components/InventoryTable";
import ReplenishmentPanel from "@/components/ReplenishmentPanel";
import VariantDrawer from "@/components/VariantDrawer";
import { useInventory } from "@/hooks/useInventory";
import { useSyncInventory } from "@/hooks/useSyncInventory";
import type { InventoryFilters } from "@/lib/types";

export default function DashboardView() {
  const [filters, setFilters] = useState<InventoryFilters>({});
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const inventory = useInventory(filters);
  const sync = useSyncInventory();

  // The selected variant is looked up from the latest data, so the drawer also shows the updated stock.
  const selected = inventory.data?.items.find((item) => item.variant_id === selectedId) ?? null;

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8 sm:px-6">
      <header>
        <h1 className="text-2xl font-semibold">Inventory</h1>
        <p className="text-sm text-zinc-500">Stock by location. Odoo is the system of record.</p>
      </header>

      <FilterBar
        value={filters}
        onChange={setFilters}
        onRefresh={() => sync.mutate()}
        refreshing={sync.isPending || (inventory.isFetching && !inventory.isPending)}
        lastUpdated={inventory.data?.as_of}
        syncError={sync.isError ? sync.error.message : undefined}
      />

      {inventory.isPending && (
        <div aria-busy="true" aria-label="Loading inventory" className="flex flex-col gap-3">
          {[0, 1, 2, 3].map((n) => (
            <div key={n} className="h-14 animate-pulse rounded-md bg-zinc-200 dark:bg-zinc-800" />
          ))}
        </div>
      )}

      {inventory.isError && (
        <div role="alert" className="rounded-md border border-red-300 p-4 dark:border-red-900">
          <p className="font-medium text-red-700 dark:text-red-400">Could not load inventory</p>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{inventory.error.message}</p>
          <button
            type="button"
            onClick={() => inventory.refetch()}
            className="mt-3 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
          >
            Try again
          </button>
        </div>
      )}

      {inventory.isSuccess && inventory.data.items.length === 0 && (
        <p className="rounded-md border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700">
          No products match the current search and filters.
        </p>
      )}

      {inventory.isSuccess && inventory.data.items.length > 0 && (
        <p className="text-sm text-zinc-500">
          To record a sale, press <strong>Record sale</strong> on a product row (or click the product name). The sale form,
          stock per location and recent movements open in a side panel.
        </p>
      )}

      {inventory.isSuccess && inventory.data.items.length > 0 && (
        <InventoryTable items={inventory.data.items} onSelect={(item) => setSelectedId(item.variant_id)} />
      )}

      <ReplenishmentPanel />

      {selected && <VariantDrawer item={selected} onClose={() => setSelectedId(null)} />}
    </main>
  );
}
