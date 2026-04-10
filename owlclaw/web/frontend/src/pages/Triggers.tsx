import { useTriggers } from "@/hooks/useApi";
import { EmptyState } from "@/components/data/EmptyState";
import { PageShell } from "@/pages/PageShell";

function statusClass(status: "success" | "failed" | "running"): string {
  if (status === "success") {
    return "border-emerald-400/40 bg-emerald-500/20 text-emerald-300";
  }
  if (status === "failed") {
    return "border-red-400/40 bg-red-500/20 text-red-300";
  }
  return "border-amber-400/40 bg-amber-500/20 text-amber-300";
}

export function TriggersPage() {
  const { data, isLoading, isError, error } = useTriggers();

  if (isLoading) {
    return <PageShell title="Triggers" description="Loading triggers..." />;
  }

  if (isError || !data) {
    return (
      <PageShell title="Triggers" description={`Failed to load triggers${error instanceof Error ? `: ${error.message}` : "."}`} />
    );
  }

  return (
    <PageShell title="Triggers" description="Unified runtime status for all six trigger types and recent execution state.">
      <section className="rounded-xl border border-border/70 bg-card/90 p-4">
        <h2 className="text-sm font-semibold">Trigger List</h2>
        {data.triggers.length === 0 ? (
          <div className="mt-3">
            <EmptyState title="No triggers configured" description="Register cron/webhook/queue triggers to start automation." />
          </div>
        ) : (
          <ul className="mt-3 space-y-2">
            {data.triggers.map((item) => (
              <li key={item.id} className="rounded-md border border-border/60 bg-background/70 px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-medium">
                    {item.name} <span className="text-xs text-muted-foreground">({item.type})</span>
                  </p>
                  <span className={`rounded border px-2 py-0.5 text-xs ${statusClass(item.last_status)}`}>{item.last_status}</span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">Next run: {item.next_run || "-"}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </PageShell>
  );
}
