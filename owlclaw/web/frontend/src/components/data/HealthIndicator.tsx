import { type HealthCheck, type HealthStatus } from "@/hooks/useApi";
import { cn } from "@/lib/utils";

type HealthIndicatorProps = {
  status: HealthStatus;
  checks: HealthCheck[];
};

const STATUS_STYLE: Record<HealthStatus, string> = {
  healthy: "bg-emerald-500/20 text-emerald-300 border-emerald-400/30",
  degraded: "bg-amber-500/20 text-amber-300 border-amber-400/30",
  unhealthy: "bg-red-500/20 text-red-300 border-red-400/30",
};

function getStatusLabel(status: HealthStatus): string {
  if (status === "healthy") {
    return "Healthy";
  }
  if (status === "degraded") {
    return "Degraded";
  }
  return "Unhealthy";
}

export function HealthIndicator({ status, checks }: HealthIndicatorProps) {
  return (
    <section className="rounded-xl border border-border/70 bg-card/90 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">System Health</h2>
        <span className={cn("rounded-full border px-3 py-1 text-xs font-medium", STATUS_STYLE[status])}>
          {getStatusLabel(status)}
        </span>
      </div>
      <ul className="mt-4 grid gap-2 sm:grid-cols-2">
        {checks.map((item) => (
          <li key={item.component} className="rounded-md border border-border/60 bg-background/70 px-3 py-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="font-medium text-foreground">{item.component}</span>
              <span className={cn(item.healthy ? "text-emerald-300" : "text-red-300")}>
                {item.healthy ? "OK" : "Down"}
              </span>
            </div>
            {item.latency_ms !== undefined && item.latency_ms !== null && (
              <p className="mt-1 text-muted-foreground">{item.latency_ms.toFixed(0)} ms</p>
            )}
            {item.message && <p className="mt-1 text-muted-foreground">{item.message}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
