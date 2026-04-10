import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";

const OverviewPage = lazy(() => import("@/pages/Overview").then((module) => ({ default: module.OverviewPage })));
const AgentsPage = lazy(() => import("@/pages/Agents").then((module) => ({ default: module.AgentsPage })));
const GovernancePage = lazy(() => import("@/pages/Governance").then((module) => ({ default: module.GovernancePage })));
const CapabilitiesPage = lazy(() => import("@/pages/Capabilities").then((module) => ({ default: module.CapabilitiesPage })));
const TriggersPage = lazy(() => import("@/pages/Triggers").then((module) => ({ default: module.TriggersPage })));
const LedgerPage = lazy(() => import("@/pages/Ledger").then((module) => ({ default: module.LedgerPage })));
const SettingsPage = lazy(() => import("@/pages/Settings").then((module) => ({ default: module.SettingsPage })));
const ExternalDashboardPage = lazy(() =>
  import("@/pages/ExternalDashboard").then((module) => ({ default: module.ExternalDashboardPage }))
);

function App() {
  return (
    <Layout>
      <Suspense fallback={<div className="rounded-md border border-border/70 bg-card/80 p-4 text-sm">Loading page...</div>}>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/governance" element={<GovernancePage />} />
          <Route path="/capabilities" element={<CapabilitiesPage />} />
          <Route path="/triggers" element={<TriggersPage />} />
          <Route path="/ledger" element={<LedgerPage />} />
          <Route path="/traces" element={<ExternalDashboardPage kind="traces" />} />
          <Route path="/workflows" element={<ExternalDashboardPage kind="workflows" />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate replace to="/" />} />
        </Routes>
      </Suspense>
    </Layout>
  );
}

export default App;
