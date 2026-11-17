"use client";

// "Refresh" mutation: triggers stock synchronisation from Odoo in the backend, then reloads the view.
//
// Purpose: the refresh button does not merely refetch local data; it asks the backend to pull
// the latest stock from Odoo (POST /api/inventory/sync) and, on success, invalidates all
// inventory and history queries. If Odoo is unavailable, the backend replies 502 and the error
// is displayed by the caller; existing local data is unchanged.
//
// Related: uses lib/api.ts; used by components/FilterBar.tsx via app/page.tsx.
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiPost } from "@/lib/api";

export function useSyncInventory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost<{ variants: number; locations: number; adjustments: number }>("/api/inventory/sync"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["movements"] });
    },
  });
}
