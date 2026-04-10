import { useMemo, useState } from "react";
import { BudgetTrend } from "@/components/charts/BudgetTrend";
import { CircuitBreakerCard } from "@/components/data/CircuitBreakerCard";
import { VisibilityMatrix } from "@/components/data/VisibilityMatrix";
import { useGovernance } from "@/hooks/useApi";
import { PageShell } from "@/pages/PageShell";

type Granularity = "day" | "week" | "month";

const GRANULARITIES: Granularity[] = ["day", "week", "month"];

function MigrationWeightBar({ value }: { value: number }) {
  const percent = Math.max(0, Math.min(100, value * 100));
  return (
    <section className="rounded-xl border border-border/70 bg-card/90 p-4">
      <h2 className="text-sm font-semibold">Migration Weight</h2>
      <p className="mt-2 text-xs text-muted-foreground">Progressive migration from legacy path to AI-native execution.</p>
      <div className="mt-3 h-3 w-full overflow-hidden rounded-full bg-muted/60">
        <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${percent}%` }} />
      </div>
      <p className="mt-2 text-xs font-medium">{percent.toFixed(1)}%</p>
    </section>
  );
}

function SkillsRank({ items }: { items: { skill: string; score: number }[] }) {
  const sorted = useMemo(() => [...items].sort((a, b) => b.score - a.score), [items]);

  return (
    <section className="rounded-xl border border-border/70 bg-card/90 p-4">
      <h2 className="text-sm font-semibold">Skills Quality Ranking</h2>
      <ol className="mt-3 space-y-2">
        {sorted.map((item) => (
          <li key={item.skill} className="flex items-center justify-between rounded-md border border-border/60 px-3 py-2">
            <span className="text-sm">{item.skill}</span>
            <span className="text-xs font-medium text-primary">{item.score.toFixed(1)}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function GovernancePage() {
  const [granularity, setGranularity] = useState<Granularity>("day");
  const { data, isLoading, isError, error } = useGovernance(granularity);

  if (isLoading) {
    return <PageShell title="Governance" description="Loading governance metrics..." />;
  }

  if (isError || !data) {
    return (
      <PageShell
        title="Governance"
        description={`Failed to load governance data${error instanceof Error ? `: ${error.message}` : "."}`}
      />
    );
  }

  return (
    <PageShell title="Governance" description="Budget, breaker states, capability visibility, and migration quality signals.">
      <div className="flex flex-wrap gap-2">
        {GRANULARITIES.map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => setGranularity(item)}
            className={`rounded-md border px-3 py-1.5 text-xs uppercase tracking-[0.12em] ${granularity === item ? "border-primary/50 bg-primary/15 text-foreground" : "border-border/70 bg-background/70 text-muted-foreground"}`}
          >
            {item}
          </button>
        ))}
      </div>
      <BudgetTrend data={data.budget_trend} />
      <div className="grid gap-3 lg:grid-cols-2">
        <CircuitBreakerCard items={data.circuit_breakers} />
        <MigrationWeightBar value={data.migration_weight} />
      </div>
      <VisibilityMatrix rows={data.visibility} />
      <SkillsRank items={data.skills_quality_rank} />
    </PageShell>
  );
}
