// "User" page: details of the signed-in account and password change.
//
// Related: components/UserProfile.tsx (username and session expiry time), components/ChangePasswordForm.tsx.
// Displayed only after login (components/AuthGate.tsx). `instant = false`: see app/page.tsx.
import ChangePasswordForm from "@/components/ChangePasswordForm";
import UserProfile from "@/components/UserProfile";

export const instant = false;

export default function AccountPage() {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-8 sm:px-6">
      <header>
        <h1 className="text-2xl font-semibold">User</h1>
        <p className="text-sm text-zinc-500">Account details and password for this application.</p>
      </header>
      <UserProfile />
      <ChangePasswordForm />
    </main>
  );
}
