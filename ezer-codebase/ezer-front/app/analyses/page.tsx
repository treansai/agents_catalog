import { getDashboardSnapshot } from "@/lib/ezer-api";

import { Dashboard } from "../dashboard";

export const dynamic = "force-dynamic";

/** Vue détaillée des analyses, en complément de la console vocale. */
export default async function AnalysesPage() {
  const snapshot = await getDashboardSnapshot();

  return <Dashboard initialSnapshot={snapshot} />;
}
