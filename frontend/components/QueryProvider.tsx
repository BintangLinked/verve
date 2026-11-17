"use client";

// TanStack Query provider for the whole application.
//
// Purpose: creates one QueryClient per browser session so the query cache is shared between components.
// retry: 1 so that network failures surface quickly as an error state (with a retry button),
// rather than waiting through multiple retries.
//
// Admin session expiry: every query OR mutation that receives 401 UNAUTHENTICATED marks the user
// as "signed out" and removes the Odoo connection data from the cache, so the panel immediately returns to
// requiring login (AuthGate displays the login page; no stale data or non-functional buttons).
//
// Related: mounted in app/layout.tsx, wrapping all pages and hooks/*.
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError } from "@/lib/api";

function expireSession(client: QueryClient, error: unknown) {
  if (error instanceof ApiError && error.status === 401 && error.code === "UNAUTHENTICATED") {
    client.setQueryData(["auth", "me"], null);
    client.removeQueries({ queryKey: ["odoo-connection"] });
    client.removeQueries({ queryKey: ["odoo-config"] });
  }
}

export default function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => {
    const created: QueryClient = new QueryClient({
      queryCache: new QueryCache({ onError: (error) => expireSession(created, error) }),
      mutationCache: new MutationCache({ onError: (error) => expireSession(created, error) }),
      defaultOptions: { queries: { retry: 1 } },
    });
    return created;
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
