"use client";

// Stock table per variant with one column per location (central warehouse, Store 1, Store 2).
//
// Purpose: displays sellable stock, low-stock status, and damaged stock (separate) in
// a single view. On wide screens it is a table; below the `md` breakpoint it becomes cards
// so that it remains readable on mobile. Clicking the product name or the "Record sale" button opens the detail drawer, which
// contains the sale form (keyboard: the buttons are focusable).
//
// Related: receives data from app/page.tsx (result of hooks/useInventory.ts); uses StatusBadge;
// calls onSelect to open components/VariantDrawer.tsx.
import StatusBadge from "@/components/StatusBadge";
import { LOCATION_LABEL, LOCATION_ORDER, type InventoryItem, type StockCell } from "@/lib/types";

interface Props {
  items: InventoryItem[];
  onSelect: (item: InventoryItem) => void;
}

// Displayed columns: fixed order, only locations present in the data (e.g. a single store when filtered).
function visibleLocations(items: InventoryItem[]): string[] {
  const present = new Set(items.flatMap((item) => item.stock.map((cell) => cell.location_code)));
  return LOCATION_ORDER.filter((code) => present.has(code));
}

function cellFor(item: InventoryItem, code: string): StockCell | undefined {
  return item.stock.find((cell) => cell.location_code === code);
}

const saleButtonClass =
  "rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800";

function StockCellView({ cell }: { cell?: StockCell }) {
  if (!cell) return <span className="text-zinc-400">-</span>;
  return (
    <div className="flex items-center gap-2">
      <span className="min-w-8 text-base font-semibold tabular-nums">{cell.available}</span>
      <StatusBadge status={cell.status} />
      {cell.damaged > 0 && <span className="text-xs text-zinc-500">{cell.damaged} damaged</span>}
    </div>
  );
}

export default function InventoryTable({ items, onSelect }: Props) {
  const locations = visibleLocations(items);

  return (
    <>
      <table className="hidden w-full text-left text-sm md:table">
        <thead className="border-b border-zinc-200 text-xs uppercase text-zinc-500 dark:border-zinc-800">
          <tr>
            <th className="py-2 pr-4 font-medium">Product</th>
            <th className="py-2 pr-4 font-medium">SKU</th>
            {locations.map((code) => (
              <th key={code} className="py-2 pr-4 font-medium">
                {LOCATION_LABEL[code] ?? code}
              </th>
            ))}
            <th className="py-2 pr-4 font-medium">Threshold</th>
            <th className="py-2 font-medium">Action</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.variant_id} className="border-b border-zinc-100 dark:border-zinc-900">
              <td className="py-3 pr-4">
                <button
                  type="button"
                  onClick={() => onSelect(item)}
                  className="text-left font-medium underline-offset-2 hover:underline"
                >
                  {item.product_name}
                  <span className="block text-xs font-normal text-zinc-500">{item.variant_name}</span>
                </button>
              </td>
              <td className="py-3 pr-4 font-mono text-xs text-zinc-500">{item.sku}</td>
              {locations.map((code) => (
                <td key={code} className="py-3 pr-4">
                  <StockCellView cell={cellFor(item, code)} />
                </td>
              ))}
              <td className="py-3 pr-4 tabular-nums text-zinc-500">{item.threshold}</td>
              <td className="py-3">
                <button
                  type="button"
                  onClick={() => onSelect(item)}
                  aria-label={`Record sale for ${item.product_name} ${item.variant_name}`}
                  className={saleButtonClass}
                >
                  Record sale
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <ul className="flex flex-col gap-3 md:hidden">
        {items.map((item) => (
          <li key={item.variant_id} className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
            <button type="button" onClick={() => onSelect(item)} className="w-full text-left font-medium">
              {item.product_name}
              <span className="block text-xs font-normal text-zinc-500">
                {item.variant_name} / {item.sku}
              </span>
            </button>
            <dl className="mt-3 grid grid-cols-1 gap-2">
              {locations.map((code) => (
                <div key={code} className="flex items-center justify-between">
                  <dt className="text-xs text-zinc-500">{LOCATION_LABEL[code] ?? code}</dt>
                  <dd>
                    <StockCellView cell={cellFor(item, code)} />
                  </dd>
                </div>
              ))}
            </dl>
            <button
              type="button"
              onClick={() => onSelect(item)}
              aria-label={`Record sale for ${item.product_name} ${item.variant_name}`}
              className={`${saleButtonClass} mt-3`}
            >
              Record sale
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}
