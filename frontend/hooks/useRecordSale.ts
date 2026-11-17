"use client";

// Sale recording mutation (POST /api/sales).
//
// Purpose: after a sale succeeds, all stock-dependent data is invalidated
// (inventory, movement history, replenishment list) so the dashboard immediately reloads
// the latest figures without a page reload. This addresses "POS sells, but the view is outdated".
//
// request_id (idempotency key) is created by the caller (components/SaleForm.tsx), not here,
// because the key must remain the same when the user repeats the same sale after a timeout.
//
// Related: uses lib/api.ts; used by components/SaleForm.tsx.
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiPost } from "@/lib/api";
import type { SaleBody, SaleResponse } from "@/lib/types";

export function useRecordSale() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SaleBody) => apiPost<SaleResponse>("/api/sales", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["movements"] });
      queryClient.invalidateQueries({ queryKey: ["replenishment"] });
    },
  });
}
