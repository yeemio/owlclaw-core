import { type AgentDetail } from "@/hooks/useApi";

type AgentDetailPanelProps = {
  detail: AgentDetail | null;
  isLoading: boolean;
};

function StatusBadge({ status }: { status: AgentDetail["status"] }) {
  const className =
    status === "active"
      ? "border-emerald-400/40 bg-emerald-500/20 text-emerald-300"
      : status === "error"
        ? "border-red-400/40 bg-red-500/20 text-red-300"
        : "border-amber-400/40 bg-amber-500/20 text-amber-300";
  return <span className={`rounded border px-2 py-0.5 text-xs ${className}`}>{status}</span>;
}

export function AgentDetailPanel({ detail, isLoading }: AgentDetailPanelProps) {
  if (isLoading) {
    return (
      <section className="rounded-xl border border-border/70 bg-card/90 p-4">
        <h2 className="text-sm font-semibold">Agent Detail</h2>
        <p className="mt-2 text-sm text-muted-foreground">Loading detail...</p>
      </section>
    );
  }

  if (!detail) {
    return (
      <section className="rounded-xl border border-border/70 bg-card/90 p-4">
        <h2 className="text-sm font-semibold">Agent Detail</h2>
        <p className="mt-2 text-sm text-muted-foreground">Select an agent card to view identity, memory, and run history.</p>
      </section>
    );
  }

  return (
    <section className="rounded-xl border border-border/70 bg-card/90 p-4">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{detail.name}</h2>
          <p className="text-xs text-muted-foreground">{detail.role}</p>
        </div>
        <StatusBadge status={detail.status} />
      </div>

      <div className="mt-4 space-y-4">
        <div>
          <h3 className="text-xs uppercase tracking-[0.12em] text-muted-foreground">Identity Config</h3>
          <div className="mt-2 space-y-1 text-sm">
            {Object.entries(detail.identity).map(([key, value]) => (
              <p key={key} className="rounded border border-border/60 bg-background/70 px-2 py-1">
                <span className="text-muted-foreground">{key}: </span>
                <span>{value}</span>
              </p>
            ))}
          </div>
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded border border-border/60 bg-background/70 px-3 py-2">
            <p className="text-xs text-muted-foreground">STM Records</p>
            <p className="text-sm font-medium">{detail.memory.short_term_count}</p>
          </div>
          <div className="rounded border border-border/60 bg-background/70 px-3 py-2">
            <p className="text-xs text-muted-foreground">LTM Records</p>
            <p className="text-sm font-medium">{detail.memory.long_term_count}</p>
          </div>
        </div>

        <div>
          <h3 className="text-xs uppercase tracking-[0.12em] text-muted-foreground">Knowledge</h3>
          <p className="mt-1 text-xs text-muted-foreground">References: {detail.knowledge.references_count}</p>
          <div className="mt-2 flex flex-wrap gap-1">
            {detail.knowledge.skills.map((skill) => (
              <span key={skill} className="rounded border border-border/60 bg-background/70 px-2 py-1 text-xs">
                {skill}
              </span>
            ))}
          </div>
        </div>

        <div>
          <h3 className="text-xs uppercase tracking-[0.12em] text-muted-foreground">Recent Runs</h3>
          <ul className="mt-2 space-y-1">
            {detail.recent_runs.map((run, index) => (
              <li key={`${run.timestamp}-${index}`} className="rounded border border-border/60 bg-background/70 px-2 py-1 text-xs">
                {run.timestamp} | {run.capability} | {run.status} | ${run.cost_usd.toFixed(4)}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
