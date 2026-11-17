"use client";

// Hook for fetching the stock list.
//
// Purpose: addresses the problem "POS sells goods but the inventory response is cached":
// - a short staleTime (5 seconds) and refetchInterval (15 seconds) keep the data fresh,
// - every mutation (sale, sync) calls invalidateQueries({ queryKey: ["inventory"] }),
// - the backend also sends Cache-Control: no-store, so there is no intermediate cache.
// placeholderData retains the old data while the filter changes so that the table does not flicker.
//
// Related: uses lib/api.ts; used by app/page.tsx. The query key ["inventory", filters] is also
// invalidated by hooks/useSyncInventory.ts and (Task 8) hooks/useRecordSale.ts.
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { InventoryFilters, InventoryResponse } from "@/lib/types";

export function useInventory(filters: InventoryFilters) {
  return useQuery({
    queryKey: ["inventory", filters],
    queryFn: () =>
      apiGet<InventoryResponse>("/api/inventory", {
        store: filters.store,
        q: filters.q,
        status: filters.status,
      }),
    staleTime: 5_000,
    refetchInterval: 15_000,
    placeholderData: keepPreviousData,
  });
}
