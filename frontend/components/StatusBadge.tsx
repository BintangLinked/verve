// Stock status badge: OK / Low / Out, or a "-" mark for the central warehouse (no threshold).
//
// Purpose: status is displayed with TEXT and colour together, so it remains readable for users
// who cannot distinguish colours. Related: used by InventoryTable and VariantDrawer.
import type { StockStatus } from "@/lib/types";

const STYLES: Record<StockStatus, { label: string; className: string }> = {
  OK: { label: "OK", className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300" },
  LOW: { label: "Low", className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
  OUT: { label: "Out", className: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" },
};

export default function StatusBadge({ status }: { status: StockStatus | null }) {
  if (status === null) return <span className="text-xs text-zinc-400" aria-label="No threshold">-</span>;
  const { label, className } = STYLES[status];
  return <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${className}`}>{label}</span>;
}
