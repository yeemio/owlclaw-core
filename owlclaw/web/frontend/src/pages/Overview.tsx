import { useEffect } from "react";
import { MetricCard } from "@/components/charts/MetricCard";
import { AlertBanner } from "@/components/data/AlertBanner";
import { HealthIndicator } from "@/components/data/HealthIndicator";
import { OnboardingCard } from "@/components/data/OnboardingCard";
import { useToast } from "@/components/system/useToast";
import { type HealthStatus, useOverview } from "@/hooks/useApi";
import { useConsoleWebSocket } from "@/hooks/useWebSocket";
import { PageShell } from "@/pages/PageShell";

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function deriveHealthStatus(okCount: number, totalCount: number): HealthStatus {
  if (totalCount === 0) {
    return "degraded";
  }
  if (okCount === totalCount) {
    return "healthy";
  }
  if (okCount > 0) {
    return "degraded";
  }
  return "unhealthy";
}

export function OverviewPage() {
  const { pushToast } = useToast();
  useConsoleWebSocket();
  const { data, isLoading, isError, error } = useOverview();

  useEffect(() => {
    if (isError) {
      pushToast("Overview request failed");
    }
  }, [isError, pushToast]);

  if (isLoading) {
    return <PageShell title="Overview" description="Loading overview metrics..." />;
  }

  if (isError || !data) {
    return (
      <PageShell
        title="Overview"
        description={`Failed to load overview metrics${error instanceof Error ? `: ${error.message}` : "."}`}
      />
    );
  }

  const healthyCount = data.health_checks.filter((item) => item.healthy).length;
  const healthStatus = deriveHealthStatus(healthyCount, data.health_checks.length);

  return (
    <PageShell title="Overview" description="Real-time system health and key runtime signals for the current tenant.">
      <AlertBanner alerts={data.alerts} />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Cost Today" value={formatCurrency(data.total_cost_today)} />
        <MetricCard label="Executions Today" value={data.total_executions_today.toLocaleString()} />
        <MetricCard label="Success Rate" value={formatPercent(data.success_rate_today)} />
        <MetricCard label="Active Agents" value={data.active_agents.toLocaleString()} />
      </div>
      <HealthIndicator status={healthStatus} checks={data.health_checks} />
      <OnboardingCard />
    </PageShell>
  );
}
