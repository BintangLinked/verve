"use client";

// Verve application login form (username and password). This is NOT the Odoo login: Odoo credentials are configured
// in the Odoo Configuration menu and are never typed here.
//
// Purpose: credentials are sent to the backend via POST /api/auth/login and the backend responds with an
// httpOnly cookie; the frontend does not store the password or token. The password is cleared from state after
// submission. The error message does not distinguish a wrong username from a wrong password (same as the backend).
//
// Related: uses hooks/useAuth.ts (useLogin); mounted in components/AuthGate.tsx (login page).
import { useState } from "react";

import { useLogin } from "@/hooks/useAuth";
import { ApiError } from "@/lib/api";

const fieldClass =
  "rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";

function message(error: Error): string {
  if (error instanceof ApiError) {
    if (error.code === "INVALID_CREDENTIALS") return "Invalid username or password.";
    if (error.code === "TOO_MANY_ATTEMPTS") return "Too many failed attempts. Try again in a few minutes.";
    if (error.code === "AUTH_NOT_CONFIGURED") return "Sign-in is not configured on the server.";
  }
  return error.message;
}

export default function LoginForm() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const login = useLogin();

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        login.mutate({ username, password });
        setPassword(""); // the password is not left in state after submission
      }}
    >
      <input
        type="text"
        autoComplete="username"
        aria-label="Username"
        placeholder="Username"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        className={fieldClass}
        required
      />
      <input
        type="password"
        autoComplete="current-password"
        aria-label="Password"
        placeholder="Password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        className={fieldClass}
        required
      />
      <button
        type="submit"
        disabled={login.isPending}
        className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-900"
      >
        {login.isPending ? "Signing in..." : "Sign in"}
      </button>
      {login.isError && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {message(login.error)}
        </p>
      )}
    </form>
  );
}
