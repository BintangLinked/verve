"use client";

// Odoo connection status hook (read-only) and the "Test connection" button.
//
// Purpose: GET /api/odoo/connection returns the status STORED in the backend (does not call Odoo),
// whereas POST /api/odoo/connection/test asks the backend to re-check using its own configuration.
// No credentials are sent from the browser: the POST body is empty. The check can take longer than
// an ordinary request (timeout and retry on the backend side), so the time limit is 30 seconds.
//
// Related: uses lib/api.ts; used by components/OdooConnectionPanel.tsx. The key ["odoo-connection"]
// is also cleared by useLogout (hooks/useAuth.ts).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api";
import type { OdooConnection } from "@/lib/types";

const KEY = ["odoo-connection"] as const;
const TEST_TIMEOUT_MS = 30_000;

export function useOdooConnection(enabled: boolean) {
  return useQuery({
    queryKey: KEY,
    queryFn: () => apiGet<OdooConnection>("/api/odoo/connection"),
    enabled,
    staleTime: 10_000,
    retry: false,
  });
}

export function useTestConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost<OdooConnection>("/api/odoo/connection/test", {}, TEST_TIMEOUT_MS),
    onSuccess: (status) => queryClient.setQueryData(KEY, status), // the test result immediately becomes the displayed status
  });
}
