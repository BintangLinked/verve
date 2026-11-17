"use client";

// Hook for the stock movement history of a single variant (for the detail drawer).
//
// Purpose: fetches the 20 most recent movements only while the drawer is open (variantId is not null).
// Related: uses lib/api.ts; used by components/VariantDrawer.tsx. The key ["movements", id]
// is invalidated after a sale/sync so that the history is also updated.
import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { Movement } from "@/lib/types";

export function useMovements(variantId: number | null) {
  return useQuery({
    queryKey: ["movements", variantId],
    queryFn: () => apiGet<Movement[]>(`/api/inventory/${variantId}/movements`, { limit: "20" }),
    enabled: variantId !== null,
    staleTime: 5_000,
  });
}
