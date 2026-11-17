"use client";

// Mutation for creating a replenishment request (POST /api/replenishment/request).
//
// Purpose: after a request is created, the replenishment list is invalidated so that the row
// is immediately marked "pending". The backend rejects a second request for a store/variant pair
// that is still pending (409 OPEN_REQUEST_EXISTS), so a double click does not create a duplicate request.
// Related: uses lib/api.ts; used by components/ReplenishmentPanel.tsx.
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiPost } from "@/lib/api";
import type { ReplenishmentBody, ReplenishmentResponse } from "@/lib/types";

export function useRequestReplenishment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ReplenishmentBody) => apiPost<ReplenishmentResponse>("/api/replenishment/request", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["replenishment"] }),
  });
}
