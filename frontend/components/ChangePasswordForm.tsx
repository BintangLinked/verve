"use client";

// Application password change form (not the Odoo password).
//
// Purpose: the admin changes the password from the UI. The new password must be at least 12 characters and must be repeated. After
// success, the backend ends all sessions so the user returns to the login page. The input values are cleared
// from state after submission.
//
// Related: uses hooks/useAuth.ts (useChangePassword); mounted in app/account/page.tsx.
import { useState } from "react";

import { useChangePassword } from "@/hooks/useAuth";
import { ApiError } from "@/lib/api";

const fieldClass = "rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const MIN_LENGTH = 12;

export default function ChangePasswordForm() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const change = useChangePassword();

  const mismatch = repeat !== "" && next !== repeat;
  const tooShort = next !== "" && next.length < MIN_LENGTH;

  return (
    <section aria-labelledby="password-heading" className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <h2 id="password-heading" className="text-sm font-semibold">
        Change password
      </h2>
      <form
        className="mt-3 flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          change.mutate({ current_password: current, new_password: next });
          setCurrent("");
          setNext("");
          setRepeat("");
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          Current password
          <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} className={fieldClass} required />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          New password
          <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} className={fieldClass} required minLength={MIN_LENGTH} />
          <span className={`text-xs ${tooShort ? "text-red-600 dark:text-red-400" : "text-zinc-500"}`}>At least {MIN_LENGTH} characters.</span>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Repeat new password
          <input type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} className={fieldClass} required />
          {mismatch && <span className="text-xs text-red-600 dark:text-red-400">The passwords do not match.</span>}
        </label>
        <div>
          <button
            type="submit"
            disabled={change.isPending || mismatch || tooShort}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-900"
          >
            {change.isPending ? "Changing..." : "Change password"}
          </button>
        </div>
        {change.isError && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {change.error instanceof ApiError && change.error.code === "INVALID_CURRENT_PASSWORD"
              ? "The current password is incorrect."
              : change.error.message}
          </p>
        )}
        <p className="text-xs text-zinc-500">After changing the password you are signed out and must sign in again.</p>
      </form>
    </section>
  );
}
