"use client";

// Odoo configuration hook: read (without API key), save, and disconnect (Disconnect).
//
// Purpose: GET /api/odoo/config returns the URL, database, and the api_key_set indicator. The API key can only
// be WRITTEN via PUT; the backend stores it encrypted and never sends it back. After
// a successful save, the old connection status is discarded so the page re-checks with the new configuration.
//
// Related: uses lib/api.ts; used by components/OdooConfigForm.tsx (the /odoo page).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiDelete, apiGet, apiPut } from "@/lib/api";
import type { OdooConfig, OdooConfigInput } from "@/lib/types";

const KEY = ["odoo-config"] as const;

export function useOdooConfig() {
  return useQuery({ queryKey: KEY, queryFn: () => apiGet<OdooConfig>("/api/odoo/config"), staleTime: 10_000, retry: false });
}

export function useSaveOdooConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: OdooConfigInput) => apiPut<OdooConfig>("/api/odoo/config", input),
    onSuccess: (config) => {
      queryClient.setQueryData(KEY, config);
      queryClient.removeQueries({ queryKey: ["odoo-connection"] });
    },
  });
}

// Disconnect: the backend deletes the stored URL, database, and API key; the connection status is re-checked.
export function useDisconnectOdoo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiDelete<OdooConfig>("/api/odoo/config"),
    onSuccess: (config) => {
      queryClient.setQueryData(KEY, config);
      queryClient.invalidateQueries({ queryKey: ["odoo-connection"] });
    },
  });
}
