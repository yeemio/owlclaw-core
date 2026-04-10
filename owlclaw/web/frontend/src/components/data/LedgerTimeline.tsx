import { type LedgerRecord } from "@/hooks/useApi";
import { cn } from "@/lib/utils";

type LedgerTimelineProps = {
  records: LedgerRecord[];
  total: number;
  limit: number;
  offset: number;
  view: "table" | "timeline";
  selectedId: string | null;
  onSelect: (record: LedgerRecord) => void;
  onViewChange: (view: "table" | "timeline") => void;
  onPageChange: (nextOffset: number) => void;
};

function statusBadgeClass(status: LedgerRecord["status"]): string {
  if (status === "success") {
    return "bg-emerald-500/20 text-emerald-300 border-emerald-400/40";
  }
  if (status === "failed") {
    return "bg-red-500/20 text-red-300 border-red-400/40";
  }
  return "bg-amber-500/20 text-amber-300 border-amber-400/40";
}

function formatTimestamp(value: string): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

export function LedgerTimeline({
  records,
  total,
  limit,
  offset,
  view,
  selectedId,
  onSelect,
  onViewChange,
  onPageChange,
}: LedgerTimelineProps) {
  const canPrev = offset > 0;
  const canNext = offset + limit < total;

  return (
    <section className="rounded-xl border border-border/70 bg-card/90 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Execution Records</h2>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => onViewChange("table")}
            className={`rounded-md border px-3 py-1 text-xs ${view === "table" ? "border-primary/50 bg-primary/15" : "border-border/70 bg-background/70"}`}
          >
            Table
          </button>
          <button
            type="button"
            onClick={() => onViewChange("timeline")}
            className={`rounded-md border px-3 py-1 text-xs ${view === "timeline" ? "border-primary/50 bg-primary/15" : "border-border/70 bg-background/70"}`}
          >
            Timeline
          </button>
        </div>
      </div>

      {view === "table" ? (
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-[0.1em] text-muted-foreground">
                <th className="px-2 py-2">Time</th>
                <th className="px-2 py-2">Agent</th>
                <th className="px-2 py-2">Capability</th>
                <th className="px-2 py-2">Status</th>
                <th className="px-2 py-2">Cost</th>
                <th className="px-2 py-2">Latency</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <tr
                  key={record.id}
                  onClick={() => onSelect(record)}
                  className={cn(
                    "cursor-pointer border-t border-border/40 hover:bg-background/60",
                    selectedId === record.id && "bg-primary/10"
                  )}
                >
                  <td className="px-2 py-2">{formatTimestamp(record.timestamp)}</td>
                  <td className="px-2 py-2">{record.agent}</td>
                  <td className="px-2 py-2">{record.capability}</td>
                  <td className="px-2 py-2">
                    <span className={cn("rounded border px-2 py-0.5 text-xs", statusBadgeClass(record.status))}>
                      {record.status}
                    </span>
                  </td>
                  <td className="px-2 py-2">${record.cost_usd.toFixed(4)}</td>
                  <td className="px-2 py-2">{record.latency_ms.toFixed(0)} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ol className="mt-3 space-y-2">
          {records.map((record) => (
            <li
              key={record.id}
              onClick={() => onSelect(record)}
              className={cn(
                "cursor-pointer rounded-md border border-border/60 bg-background/60 p-3 hover:border-primary/40",
                selectedId === record.id && "border-primary/50 bg-primary/10"
              )}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium">
                  {record.agent} {"->"} {record.capability}
                </p>
                <span className={cn("rounded border px-2 py-0.5 text-xs", statusBadgeClass(record.status))}>
                  {record.status}
                </span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {formatTimestamp(record.timestamp)} | ${record.cost_usd.toFixed(4)} | {record.latency_ms.toFixed(0)} ms
              </p>
            </li>
          ))}
        </ol>
      )}

      <div className="mt-4 flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          Showing {Math.min(offset + 1, total)}-{Math.min(offset + records.length, total)} of {total}
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={!canPrev}
            onClick={() => onPageChange(Math.max(0, offset - limit))}
            className="rounded-md border border-border/70 bg-background/70 px-3 py-1.5 text-xs disabled:opacity-40"
          >
            Prev
          </button>
          <button
            type="button"
            disabled={!canNext}
            onClick={() => onPageChange(offset + limit)}
            className="rounded-md border border-border/70 bg-background/70 px-3 py-1.5 text-xs disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </section>
  );
}
