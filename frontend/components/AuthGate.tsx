"use client";

// Login gate: all pages are displayed only after login.
//
// Purpose: a user who is not signed in sees only the login page (no menu, no data). After sign-in,
// the page is wrapped in the menu (AppNav). An expired session returns to the login page automatically, because
// QueryProvider marks the user as signed out when the backend responds with 401.
//
// Related: uses hooks/useAuth.ts, components/LoginForm.tsx, components/AppNav.tsx; mounted in app/layout.tsx.
import AppNav from "@/components/AppNav";
import LoginForm from "@/components/LoginForm";
import { useAuth } from "@/hooks/useAuth";

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const auth = useAuth();

  if (auth.isPending) {
    return (
      <main className="flex flex-1 items-center justify-center p-6">
        <p className="text-sm text-zinc-500">Checking sign-in...</p>
      </main>
    );
  }

  if (auth.isError) {
    return (
      <main className="flex flex-1 items-center justify-center p-6">
        <div role="alert" className="max-w-sm rounded-md border border-red-300 p-4 text-sm dark:border-red-900">
          <p className="font-medium text-red-700 dark:text-red-400">Could not check sign-in</p>
          <p className="mt-1 text-zinc-600 dark:text-zinc-400">{auth.error.message}</p>
          <button
            type="button"
            onClick={() => auth.refetch()}
            className="mt-3 rounded-md bg-zinc-900 px-4 py-2 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
          >
            Try again
          </button>
        </div>
      </main>
    );
  }

  if (!auth.data) {
    return (
      <main className="flex flex-1 items-center justify-center p-6">
        <div className="w-full max-w-sm rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
          <h1 className="text-xl font-semibold">Verve Inventory</h1>
          <p className="mb-4 mt-1 text-sm text-zinc-500">Sign in to continue.</p>
          <LoginForm />
        </div>
      </main>
    );
  }

  return (
    <>
      <AppNav username={auth.data.username} />
      {children}
    </>
  );
}
