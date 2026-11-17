"use client";

// Profile card: username, role, and session expiry time for the signed-in account.
//
// Related: hooks/useAuth.ts (GET /api/auth/me); mounted in app/account/page.tsx.
import { useAuth } from "@/hooks/useAuth";

export default function UserProfile() {
  const user = useAuth().data;
  return (
    <section aria-labelledby="profile-heading" className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <h2 id="profile-heading" className="text-sm font-semibold">
        Profile
      </h2>
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs text-zinc-500">Username</dt>
          <dd>{user?.username ?? "-"}</dd>
        </div>
        <div>
          <dt className="text-xs text-zinc-500">Role</dt>
          <dd>Administrator</dd>
        </div>
        <div>
          <dt className="text-xs text-zinc-500">Session expires</dt>
          <dd>{user?.expires_at ? new Date(user.expires_at).toLocaleString() : "-"}</dd>
        </div>
      </dl>
    </section>
  );
}
