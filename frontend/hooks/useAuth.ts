"use client";

// Admin authentication hook: who is currently logged in, login, and logout.
//
// Purpose: the backend uses an httpOnly session cookie, so the frontend never holds a token or
// credentials; it only asks "who am I?" (GET /api/auth/me). A 401 response means "not logged in"
// (not an error), so useAuth returns null.
//
// Related: uses lib/api.ts; used by components/OdooConnectionPanel.tsx and components/LoginForm.tsx.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, apiGet, apiPost, apiPut } from "@/lib/api";
import type { AuthUser } from "@/lib/types";

const ME_KEY = ["auth", "me"] as const;

export function useAuth() {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: async (): Promise<AuthUser | null> => {
      try {
        return await apiGet<AuthUser>("/api/auth/me");
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 60_000,
    retry: false,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (credentials: { username: string; password: string }) =>
      apiPost<AuthUser>("/api/auth/login", credentials),
    onSuccess: (user) => {
      queryClient.setQueryData(ME_KEY, user);
      queryClient.invalidateQueries({ queryKey: ME_KEY }); // the login response has no session time; reload from /api/auth/me
      queryClient.invalidateQueries({ queryKey: ["odoo-connection"] });
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost<void>("/api/auth/logout"),
    onSuccess: () => {
      queryClient.setQueryData(ME_KEY, null);
      queryClient.removeQueries({ queryKey: ["odoo-connection"] }); // no connection data remains in the cache
      queryClient.removeQueries({ queryKey: ["odoo-config"] });
    },
  });
}

// Change the admin password. The backend ends all sessions afterwards, so the user returns to the login page.
export function useChangePassword() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) => apiPut<void>("/api/auth/password", body),
    onSuccess: () => {
      queryClient.setQueryData(ME_KEY, null);
      queryClient.removeQueries({ queryKey: ["odoo-connection"] });
      queryClient.removeQueries({ queryKey: ["odoo-config"] });
    },
  });
}
