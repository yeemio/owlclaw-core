import { useState } from "react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { EmptyState } from "@/components/data/EmptyState";
import { SchemaViewer } from "@/components/data/SchemaViewer";
import { type CapabilityCategory, useCapabilities } from "@/hooks/useApi";
import { PageShell } from "@/pages/PageShell";

type TabName = "handlers" | "skills" | "bindings" | "scan" | "migration";

const TAB_META: { key: TabName; label: string }[] = [
  { key: "handlers", label: "Handlers" },
  { key: "skills", label: "Skills" },
  { key: "bindings", label: "Bindings" },
  { key: "scan", label: "Scan Result" },
  { key: "migration", label: "Migration Progress" },
];

function categoryForTab(tab: TabName): CapabilityCategory | null {
  if (tab === "handlers") {
    return "handler";
  }
  if (tab === "skills") {
    return "skill";
  }
  if (tab === "bindings") {
    return "binding";
  }
  return null;
}

export function CapabilitiesPage() {
  const [tab, setTab] = useState<TabName>("handlers");
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const { data, isLoading, isError, error } = useCapabilities();

  if (isLoading) {
    return <PageShell title="Capabilities" description="Loading capabilities..." />;
  }

  if (isError || !data) {
    return (
      <PageShell
        title="Capabilities"
        description={`Failed to load capabilities${error instanceof Error ? `: ${error.message}` : "."}`}
      />
    );
  }

  const category = categoryForTab(tab);
  const filtered = category ? data.capabilities.filter((item) => item.category === category) : data.capabilities;
  const selected = filtered.find((item) => item.name === selectedName) ?? filtered[0] ?? null;
  const chartData = filtered.map((item) => ({
    name: item.name.length > 18 ? `${item.name.slice(0, 18)}...` : item.name,
    executions: item.stats.executions,
    successRate: Math.round(item.stats.success_rate * 100),
  }));

  return (
    <PageShell title="Capabilities" description="Capability catalog, schema inspection, and migration visibility.">
      <div className="flex flex-wrap gap-2">
        {TAB_META.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => {
              setTab(item.key);
              setSelectedName(null);
            }}
            className={`rounded-md border px-3 py-1.5 text-xs ${tab === item.key ? "border-primary/50 bg-primary/15" : "border-border/70 bg-background/70 text-muted-foreground"}`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "scan" ? (
        <section className="rounded-xl border border-border/70 bg-card/90 p-4">
          <h2 className="text-sm font-semibold">Scan Candidates</h2>
          {data.scan_candidates.length === 0 ? (
            <div className="mt-3">
              <EmptyState title="No scan candidates" description="Run cli-scan to discover candidate functions." />
            </div>
          ) : (
            <ul className="mt-3 space-y-2 text-sm">
              {data.scan_candidates.map((item, idx) => (
                <li key={`${item.module}-${item.function}-${idx}`} className="rounded border border-border/60 bg-background/70 px-3 py-2">
                  <p className="font-medium">{item.function}</p>
                  <p className="text-xs text-muted-foreground">{item.module}</p>
                  <pre className="mt-1 text-xs text-muted-foreground">{item.signature}</pre>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : tab === "migration" ? (
        <section className="rounded-xl border border-border/70 bg-card/90 p-4">
          <h2 className="text-sm font-semibold">Migration Progress</h2>
          {data.migration_progress.length === 0 ? (
            <div className="mt-3">
              <EmptyState title="No migration data" description="Run cli-migrate to generate migration progress artifacts." />
            </div>
          ) : (
            <ul className="mt-3 space-y-2 text-sm">
              {data.migration_progress.map((item) => (
                <li key={item.capability} className="flex items-center justify-between rounded border border-border/60 bg-background/70 px-3 py-2">
                  <span>{item.capability}</span>
                  <span className="text-xs text-muted-foreground">{item.status}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : (
        <div className="grid gap-3 xl:grid-cols-[1.1fr_1fr]">
          <section className="rounded-xl border border-border/70 bg-card/90 p-4">
            <h2 className="text-sm font-semibold">Capability List</h2>
            <div className="mt-3 space-y-2">
              {filtered.map((item) => (
                <button
                  key={item.name}
                  type="button"
                  onClick={() => setSelectedName(item.name)}
                  className={`w-full rounded border px-3 py-2 text-left text-sm ${selected?.name === item.name ? "border-primary/50 bg-primary/10" : "border-border/60 bg-background/70 hover:border-primary/30"}`}
                >
                  <p className="font-medium">{item.name}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {item.stats.executions} runs | {(item.stats.success_rate * 100).toFixed(1)}% success |{" "}
                    {item.stats.avg_latency_ms.toFixed(0)} ms
                  </p>
                </button>
              ))}
            </div>
          </section>

          <section className="space-y-3">
            <div className="rounded-xl border border-border/70 bg-card/90 p-4">
              <h2 className="text-sm font-semibold">Schema</h2>
              {selected ? <SchemaViewer schema={selected.schema} /> : <p className="mt-2 text-sm text-muted-foreground">No capability selected.</p>}
            </div>
            <div className="rounded-xl border border-border/70 bg-card/90 p-4">
              <h2 className="text-sm font-semibold">Invocation Stats</h2>
              <div className="mt-3 h-64">
                <ResponsiveContainer>
                  <BarChart data={chartData}>
                    <XAxis dataKey="name" stroke="hsl(var(--muted-foreground))" fontSize={11} />
                    <YAxis stroke="hsl(var(--muted-foreground))" fontSize={11} />
                    <Tooltip
                      contentStyle={{
                        background: "hsl(var(--card))",
                        border: "1px solid hsl(var(--border))",
                        borderRadius: "8px",
                      }}
                    />
                    <Bar dataKey="executions" fill="hsl(var(--primary))" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </section>
        </div>
      )}
    </PageShell>
  );
}
