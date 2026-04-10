import { cn } from "@/lib/utils";

type MetricCardProps = {
  label: string;
  value: string;
  trend?: number | null;
};

function trendText(trend: number): string {
  const sign = trend > 0 ? "+" : "";
  return `${sign}${trend.toFixed(1)}%`;
}

export function MetricCard({ label, value, trend }: MetricCardProps) {
  const isPositive = (trend ?? 0) >= 0;

  return (
    <article className="rounded-xl border border-border/70 bg-card/90 p-4">
      <p className="text-xs uppercase tracking-[0.14em] text-muted-foreground">{label}</p>
      <div className="mt-3 flex items-end justify-between">
        <p className="text-2xl font-semibold tracking-tight">{value}</p>
        {typeof trend === "number" && (
          <p className={cn("text-xs font-medium", isPositive ? "text-emerald-300" : "text-red-300")}>
            {trendText(trend)}
          </p>
        )}
      </div>
    </article>
  );
}
