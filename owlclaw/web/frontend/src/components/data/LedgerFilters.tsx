import type { ChangeEvent } from "react";
import { type LedgerFilters as LedgerFiltersValue } from "@/hooks/useApi";

type LedgerFiltersProps = {
  value: LedgerFiltersValue;
  onChange: (next: LedgerFiltersValue) => void;
  onApply: () => void;
  onReset: () => void;
};

function onInputChange(
  event: ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  value: LedgerFiltersValue,
  onChange: (next: LedgerFiltersValue) => void
) {
  const { name, value: fieldValue } = event.target;
  onChange({ ...value, [name]: fieldValue });
}

export function LedgerFilters({ value, onChange, onApply, onReset }: LedgerFiltersProps) {
  return (
    <section className="rounded-xl border border-border/70 bg-card/90 p-4">
      <h2 className="text-sm font-semibold">Filters</h2>
      <div className="mt-3 grid gap-3 md:grid-cols-3 xl:grid-cols-4">
        <input
          name="agent"
          value={value.agent ?? ""}
          onChange={(event) => onInputChange(event, value, onChange)}
          placeholder="Agent"
          aria-label="Filter by agent"
          className="rounded-md border border-border/70 bg-background/70 px-3 py-2 text-sm"
        />
        <input
          name="capability"
          value={value.capability ?? ""}
          onChange={(event) => onInputChange(event, value, onChange)}
          placeholder="Capability"
          aria-label="Filter by capability"
          className="rounded-md border border-border/70 bg-background/70 px-3 py-2 text-sm"
        />
        <select
          name="status"
          value={value.status ?? ""}
          onChange={(event) => onInputChange(event, value, onChange)}
          className="rounded-md border border-border/70 bg-background/70 px-3 py-2 text-sm"
          aria-label="Filter by status"
        >
          <option value="">Any Status</option>
          <option value="success">Success</option>
          <option value="failed">Failed</option>
          <option value="running">Running</option>
        </select>
        <select
          name="order_by"
          aria-label="Order By"
          value={value.order_by ?? "created_at_desc"}
          onChange={(event) => onInputChange(event, value, onChange)}
          className="rounded-md border border-border/70 bg-background/70 px-3 py-2 text-sm"
        >
          <option value="created_at_desc">Time (newest first)</option>
          <option value="created_at_asc">Time (oldest first)</option>
          <option value="cost_desc">Cost (high to low)</option>
          <option value="cost_asc">Cost (low to high)</option>
        </select>
        <input
          name="start_time"
          type="datetime-local"
          value={value.start_time ?? ""}
          onChange={(event) => onInputChange(event, value, onChange)}
          aria-label="Start time"
          className="rounded-md border border-border/70 bg-background/70 px-3 py-2 text-sm"
        />
        <input
          name="end_time"
          type="datetime-local"
          value={value.end_time ?? ""}
          onChange={(event) => onInputChange(event, value, onChange)}
          aria-label="End time"
          className="rounded-md border border-border/70 bg-background/70 px-3 py-2 text-sm"
        />
        <input
          name="min_cost"
          type="number"
          step="0.01"
          value={value.min_cost ?? ""}
          onChange={(event) =>
            onChange({
              ...value,
              min_cost: event.target.value === "" ? undefined : Number(event.target.value),
            })
          }
          placeholder="Min cost"
          aria-label="Minimum cost"
          className="rounded-md border border-border/70 bg-background/70 px-3 py-2 text-sm"
        />
        <input
          name="max_cost"
          type="number"
          step="0.01"
          value={value.max_cost ?? ""}
          onChange={(event) =>
            onChange({
              ...value,
              max_cost: event.target.value === "" ? undefined : Number(event.target.value),
            })
          }
          placeholder="Max cost"
          aria-label="Maximum cost"
          className="rounded-md border border-border/70 bg-background/70 px-3 py-2 text-sm"
        />
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={onApply}
          className="rounded-md border border-primary/50 bg-primary/15 px-3 py-1.5 text-xs font-medium"
        >
          Apply
        </button>
        <button
          type="button"
          onClick={onReset}
          className="rounded-md border border-border/70 bg-background/70 px-3 py-1.5 text-xs font-medium"
        >
          Reset
        </button>
      </div>
    </section>
  );
}
