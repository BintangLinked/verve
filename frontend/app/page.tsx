// Main page (Dashboard): a thin server file that renders components/DashboardView.tsx.
//
// instant = false: AuthGate (app/layout.tsx) does not render the page before the user logs in, so the
// Next.js "instant navigation" validation does not apply to this page. This option may only exist in a
// server component, which is why the dashboard content is separated into a client component.
import DashboardView from "@/components/DashboardView";

export const instant = false;

export default function Page() {
  return <DashboardView />;
}
