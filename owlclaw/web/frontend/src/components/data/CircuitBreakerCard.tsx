import { type CircuitBreakerState } from "@/hooks/useApi";
import { cn } from "@/lib/utils";

type CircuitBreakerCardProps = {
  items: CircuitBreakerState[];
};

const STATE_STYLE: Record<CircuitBreakerState["state"], string> = {
  open: "border-red-400/40 bg-red-500/10 text-red-200",
  closed: "border-emerald-400/40 bg-emerald-500/10 text-emerald-200",
  half_open: "border-amber-400/40 bg-amber-500/10 text-amber-200",
};

export function CircuitBreakerCard({ items }: CircuitBreakerCardProps) {
  return (
    <section className="rounded-xl border border-border/70 bg-card/90 p-4">
      <h2 className="text-sm font-semibold">Circuit Breakers</h2>
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        {items.map((item) => (
          <div key={item.name} className={cn("rounded-md border px-3 py-2 text-sm", STATE_STYLE[item.state])}>
            <p className="font-medium">{item.name}</p>
            <p className="mt-1 text-xs uppercase tracking-[0.12em]">{item.state.replace("_", " ")}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
