// "Odoo configuration" page: Odoo connection settings and connection status.
//
// Related: components/OdooConfigForm.tsx (saves the configuration), components/OdooConnectionPanel.tsx (status
// and Test connection). Displayed only after login (components/AuthGate.tsx).
import OdooConfigForm from "@/components/OdooConfigForm";
import OdooConnectionPanel from "@/components/OdooConnectionPanel";

export const instant = false; // see app/page.tsx: the page is rendered after login

export default function OdooConfigurationPage() {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-8 sm:px-6">
      <header>
        <h1 className="text-2xl font-semibold">Odoo configuration</h1>
        <p className="text-sm text-zinc-500">
          Connection settings for the Odoo system of record. Credentials are handled by the server only.
        </p>
      </header>
      <OdooConnectionPanel />
      <OdooConfigForm />
    </main>
  );
}
