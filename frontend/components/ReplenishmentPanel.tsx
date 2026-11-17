"use client";

// Replenishment panel: list of stores/variants whose stock is below the threshold, with a "Request" button.
//
// Purpose: highlights store replenishment needs. The initial quantity = the backend's suggested quantity (2x threshold minus
// stock), which can be changed. Central warehouse stock is displayed so it is visible whether the request can be fulfilled.
// Rows that already have a PENDING request show an indicator and the button is disabled.
// Handles loading, error (with retry), empty states, and per-request errors from the backend.
//
// Related: data from hooks/useReplenishment.ts; requests via hooks/useRequestReplenishment.ts;
// mounted in app/page.tsx below the stock table.
import { useState } from "react";

import StatusBadge from "@/components/StatusBadge";
import { useReplenishment } from "@/hooks/useReplenishment";
import { useRequestReplenishment } from "@/hooks/useRequestReplenishment";
import { LOCATION_LABEL, type ReplenishmentItem } from "@/lib/types";

function Row({ item }: { item: ReplenishmentItem }) {
  const [quantity, setQuantity] = useState(String(item.suggested_quantity));
  const request = useRequestReplenishment();
  const qty = Number(quantity);
  const valid = Number.isInteger(qty) && qty >= 1 && qty <= 1000;

  return (
    <li className="flex flex-col gap-2 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-medium">
            {LOCATION_LABEL[item.store_code] ?? item.store_code}: {item.variant_name}
          </p>
          <p className="font-mono text-xs text-zinc-500">{item.sku}</p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className="font-semibold tabular-nums">{item.available}</span>
          <span className="text-zinc-500">of {item.threshold}</span>
          <StatusBadge status={item.status} />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-zinc-500">Warehouse stock: {item.warehouse_available}</span>
        {item.pending_request || request.isSuccess ? (
          <span className="rounded bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-800 dark:bg-sky-950 dark:text-sky-300">
            Request pending
          </span>
        ) : (
          <form
            className="ml-auto flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (!valid) return;
              // new request_id per click; double clicks are prevented by the backend (OPEN_REQUEST_EXISTS).
              request.mutate({
                request_id: crypto.randomUUID(),
                store_id: item.store_code,
                variant_id: item.variant_id,
                quantity: qty,
              });
            }}
          >
            <input
              type="number"
              min={1}
              max={1000}
              step={1}
              aria-label={`Quantity to request for ${item.variant_name} at ${item.store_code}`}
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              className="w-20 rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            />
            <button
              type="submit"
              disabled={!valid || request.isPending}
              className="rounded-md bg-zinc-900 px-3 py-1 text-sm font-medium text-white disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-900"
            >
              {request.isPending ? "Requesting..." : "Request"}
            </button>
          </form>
        )}
      </div>
      {request.isError && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {request.error.message}
        </p>
      )}
    </li>
  );
}

export default function ReplenishmentPanel() {
  const replenishment = useReplenishment();

  return (
    <section aria-labelledby="replenishment-heading" className="flex flex-col gap-3">
      <h2 id="replenishment-heading" className="text-lg font-semibold">
        Replenishment needed
      </h2>
      {replenishment.isPending && <p className="text-sm text-zinc-500">Loading replenishment list...</p>}
      {replenishment.isError && (
        <div role="alert" className="text-sm text-red-600 dark:text-red-400">
          {replenishment.error.message}{" "}
          <button type="button" onClick={() => replenishment.refetch()} className="underline">
            Retry
          </button>
        </div>
      )}
      {replenishment.isSuccess && replenishment.data.length === 0 && (
        <p className="text-sm text-zinc-500">All stores are at or above their thresholds.</p>
      )}
      {replenishment.isSuccess && replenishment.data.length > 0 && (
        <ul className="flex flex-col gap-3">
          {replenishment.data.map((item) => (
            <Row key={`${item.store_code}-${item.variant_id}`} item={item} />
          ))}
        </ul>
      )}
    </section>
  );
}
