"use client";

// "Record sale" form inside the detail drawer: sends one sale to the backend.
//
// Purpose: demonstrates the full flow (submit sale -> local stock decreases -> event sent to
// Odoo by the worker -> view reloaded). The POS channel selects the store; the ONLINE channel always
// deducts from the central warehouse, so the store selection is hidden.
//
// Idempotency: one sale "intent" uses one request_id. If the user presses the button again
// after a network error (timeout), the same key is reused, so the backend does not create a
// duplicate sale. Once the form content is changed, a new key is created (the old key must not be used for different
// content; the backend will reject it with 409).
//
// Related: uses hooks/useRecordSale.ts; mounted in components/VariantDrawer.tsx.
import { useState } from "react";

import { useRecordSale } from "@/hooks/useRecordSale";
import { ApiError } from "@/lib/api";
import { LOCATION_LABEL, STORE_CODES, type InventoryItem, type SaleChannel } from "@/lib/types";

const fieldClass =
  "rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";

// User-friendly error messages; INSUFFICIENT_STOCK states the available stock from the backend error details.
function errorMessage(error: Error): string {
  if (error instanceof ApiError && error.code === "INSUFFICIENT_STOCK") {
    const available = (error.details as { available?: number } | undefined)?.available;
    return available === undefined ? "Not enough stock." : `Not enough stock. Available: ${available}.`;
  }
  return error.message;
}

export default function SaleForm({ item }: { item: InventoryItem }) {
  const [channel, setChannel] = useState<SaleChannel>("POS");
  const [store, setStore] = useState<string>(STORE_CODES[0]);
  const [quantity, setQuantity] = useState("1");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const sale = useRecordSale();

  const qty = Number(quantity);
  const valid = Number.isInteger(qty) && qty >= 1 && qty <= 1000;

  // Changing the form content = a new intent: create a new request_id and clear the previous result.
  function edit(change: () => void) {
    change();
    setRequestId(crypto.randomUUID());
    sale.reset();
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid) return;
    sale.mutate(
      {
        request_id: requestId,
        variant_id: item.variant_id,
        quantity: qty,
        channel,
        store_id: channel === "POS" ? store : undefined,
      },
      // The next sale is a new intent, so a new key (the successful result remains displayed).
      { onSuccess: () => setRequestId(crypto.randomUUID()) },
    );
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <select
          aria-label="Sales channel"
          value={channel}
          onChange={(e) => edit(() => setChannel(e.target.value as SaleChannel))}
          className={fieldClass}
        >
          <option value="POS">POS (store)</option>
          <option value="ONLINE">Online (warehouse)</option>
        </select>
        {channel === "POS" && (
          <select
            aria-label="Store"
            value={store}
            onChange={(e) => edit(() => setStore(e.target.value))}
            className={fieldClass}
          >
            {STORE_CODES.map((code) => (
              <option key={code} value={code}>
                {LOCATION_LABEL[code]}
              </option>
            ))}
          </select>
        )}
        <input
          type="number"
          inputMode="numeric"
          min={1}
          max={1000}
          step={1}
          aria-label="Quantity"
          value={quantity}
          onChange={(e) => edit(() => setQuantity(e.target.value))}
          className={`${fieldClass} w-24`}
        />
        <button
          type="submit"
          disabled={!valid || sale.isPending}
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-900"
        >
          {sale.isPending ? "Recording..." : "Record sale"}
        </button>
      </div>
      {!valid && <p className="text-xs text-amber-700 dark:text-amber-400">Quantity must be a whole number from 1 to 1000.</p>}
      {sale.isSuccess && (
        <p role="status" className="text-sm text-emerald-700 dark:text-emerald-400">
          {sale.data.replayed ? "This sale was already recorded." : "Sale recorded."} Remaining at{" "}
          {LOCATION_LABEL[sale.data.location_code] ?? sale.data.location_code}: {sale.data.remaining}. Odoo sync:{" "}
          {sale.data.odoo_status}.
        </p>
      )}
      {sale.isError && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {errorMessage(sale.error)}
        </p>
      )}
    </form>
  );
}
