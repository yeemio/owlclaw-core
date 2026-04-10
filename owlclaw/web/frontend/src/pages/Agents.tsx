import { useState } from "react";
import { EmptyState } from "@/components/data/EmptyState";
import { AgentDetailPanel } from "@/pages/AgentDetail";
import { useAgentDetail, useAgents } from "@/hooks/useApi";
import { PageShell } from "@/pages/PageShell";

function AgentStatusDot({ status }: { status: "active" | "idle" | "error" }) {
  const color =
    status === "active" ? "bg-emerald-400" : status === "error" ? "bg-red-400" : "bg-amber-400";
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${color}`} />;
}

export function AgentsPage() {
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const { data: agents, isLoading, isError, error } = useAgents();
  const { data: detail, isLoading: isDetailLoading } = useAgentDetail(selectedAgentId);

  if (isLoading) {
    return <PageShell title="Agents" description="Loading agents..." />;
  }

  if (isError || !agents) {
    return (
      <PageShell title="Agents" description={`Failed to load agents${error instanceof Error ? `: ${error.message}` : "."}`} />
    );
  }

  return (
    <PageShell title="Agents" description="Agent roster, identity snapshot, and per-agent operational detail.">
      <div className="grid gap-3 xl:grid-cols-[1.2fr_1fr]">
        <section className="rounded-xl border border-border/70 bg-card/90 p-4">
          <h2 className="text-sm font-semibold">Agent Cards</h2>
          {agents.length === 0 ? (
            <div className="mt-3">
              <EmptyState title="No agents found" description="Register agents to start monitoring runtime behavior." />
            </div>
          ) : (
            <div className="mt-3 grid gap-2">
              {agents.map((agent) => (
                <button
                  key={agent.id}
                  type="button"
                  onClick={() => setSelectedAgentId(agent.id)}
                  className={`rounded-md border px-3 py-3 text-left ${selectedAgentId === agent.id ? "border-primary/50 bg-primary/10" : "border-border/60 bg-background/70 hover:border-primary/30"}`}
                >
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium">{agent.name}</p>
                    <AgentStatusDot status={agent.status} />
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{agent.role}</p>
                  <p className="mt-2 text-xs text-muted-foreground">{agent.identity_summary}</p>
                </button>
              ))}
            </div>
          )}
        </section>
        <AgentDetailPanel detail={detail ?? null} isLoading={isDetailLoading} />
      </div>
    </PageShell>
  );
}
