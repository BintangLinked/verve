"use client";

// Variant detail drawer: stock per location (including damaged stock) and the 20 most recent stock movements.
//
// Purpose: provides context behind the figures in the table, e.g. "why did SHOP1 stock decrease?" The drawer
// handles loading, error (with retry), and empty states for the history. Press Escape
// or click the backdrop to close; the close button receives focus when opened (accessibility).
//
// Related: the item comes from app/page.tsx; history from hooks/useMovements.ts; the "Record sale" form
// from components/SaleForm.tsx (after a sale, queries are invalidated so the drawer is also refreshed).
import { useEffect, useRef } from "react";

import SaleForm from "@/components/SaleForm";
import StatusBadge from "@/components/StatusBadge";
import { useMovements } from "@/hooks/useMovements";
import { LOCATION_LABEL, type InventoryItem } from "@/lib/types";

interface Props {
  item: InventoryItem;
  onClose: () => void;
}

// Human-readable names for movement types.
const MOVEMENT_LABEL: Record<string, string> = {
  SALE: "Sale",
  RETURN_DAMAGED: "Damaged return",
  REPLENISHMENT: "Replenishment",
  SYNC_ADJUSTMENT: "Odoo sync adjustment",
};

export default function VariantDrawer({ item, onClose }: Props) {
  const movements = useMovements(item.variant_id);
  const closeRef = useRef<HTMLButtonElement>(null);

  // The parent creates a new onClose on every render (and the dashboard re-renders on each poll). The effect below
  // deliberately does NOT depend on onClose: if it did, focus would be reclaimed by the Close button
  // every 15 seconds while the user is typing in the sale form.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  // Initial focus on the close button, only once when the drawer opens.
  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  // Escape closes the drawer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCloseRef.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden="true" />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`${item.product_name} ${item.variant_name}`}
        className="relative flex h-full w-full max-w-md flex-col gap-6 overflow-y-auto bg-white p-6 shadow-xl dark:bg-zinc-900"
      >
        <header className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">{item.product_name}</h2>
            <p className="text-sm text-zinc-500">
              {item.variant_name} / <span className="font-mono">{item.sku}</span>
            </p>
            <p className="text-xs text-zinc-500">Low-stock threshold: {item.threshold}</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="rounded-md border border-zinc-300 px-3 py-1 text-sm dark:border-zinc-700"
          >
            Close
          </button>
        </header>

        <section>
          <h3 className="mb-2 text-sm font-medium">Stock by location</h3>
          <ul className="flex flex-col divide-y divide-zinc-100 dark:divide-zinc-800">
            {item.stock.map((cell) => (
              <li key={cell.location_code} className="flex items-center justify-between py-2 text-sm">
                <span>{LOCATION_LABEL[cell.location_code] ?? cell.location_code}</span>
                <span className="flex items-center gap-2">
                  <span className="font-semibold tabular-nums">{cell.available}</span>
                  <StatusBadge status={cell.status} />
                  <span className="w-24 text-right text-xs text-zinc-500">
                    {cell.damaged > 0 ? `${cell.damaged} damaged` : ""}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </section>

        <section>
          <h3 className="mb-2 text-sm font-medium">Record sale</h3>
          <SaleForm item={item} />
        </section>

        <section>
          <h3 className="mb-2 text-sm font-medium">Recent movements</h3>
          {movements.isPending && <p className="text-sm text-zinc-500">Loading movements...</p>}
          {movements.isError && (
            <div role="alert" className="text-sm text-red-600 dark:text-red-400">
              {movements.error.message}{" "}
              <button type="button" onClick={() => movements.refetch()} className="underline">
                Retry
              </button>
            </div>
          )}
          {movements.isSuccess && movements.data.length === 0 && (
            <p className="text-sm text-zinc-500">No movements recorded yet.</p>
          )}
          {movements.isSuccess && movements.data.length > 0 && (
            <ul className="flex flex-col divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
              {movements.data.map((m) => (
                <li key={m.id} className="flex items-center justify-between py-2">
                  <span>
                    {MOVEMENT_LABEL[m.type] ?? m.type}
                    <span className="block text-xs text-zinc-500">
                      {LOCATION_LABEL[m.location_code] ?? m.location_code} / {m.bucket} /{" "}
                      {new Date(m.created_at).toLocaleString()}
                    </span>
                  </span>
                  <span
                    className={`font-semibold tabular-nums ${m.qty_delta < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}
                  >
                    {m.qty_delta > 0 ? `+${m.qty_delta}` : m.qty_delta}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </aside>
    </div>
  );
}
