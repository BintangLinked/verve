"use client";

// Hook for the replenishment needs list (GET /api/replenishment): store/variant pairs below the threshold.
//
// Purpose: source data for the replenishment panel. Refreshed periodically and invalidated after a sale
// or replenishment request, so the "pending" indicator and stock quantities remain current.
// Related: uses lib/api.ts; used by components/ReplenishmentPanel.tsx.
import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { ReplenishmentItem } from "@/lib/types";

export function useReplenishment() {
  return useQuery({
    queryKey: ["replenishment"],
    queryFn: () => apiGet<ReplenishmentItem[]>("/api/replenishment"),
    staleTime: 5_000,
    refetchInterval: 15_000,
  });
}
