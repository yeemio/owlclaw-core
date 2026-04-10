import { useMemo, useState } from "react";
import { EmptyState } from "@/components/data/EmptyState";
import { LedgerDetail } from "@/components/data/LedgerDetail";
import { LedgerFilters } from "@/components/data/LedgerFilters";
import { LedgerTimeline } from "@/components/data/LedgerTimeline";
import { type LedgerFilters as LedgerFiltersValue, type LedgerRecord, useLedger } from "@/hooks/useApi";
import { PageShell } from "@/pages/PageShell";

const DEFAULT_FILTERS: LedgerFiltersValue = {
  agent: "",
  capability: "",
  status: "",
  start_time: "",
  end_time: "",
  min_cost: undefined,
  max_cost: undefined,
  order_by: "created_at_desc",
};

export function LedgerPage() {
  const [draftFilters, setDraftFilters] = useState<LedgerFiltersValue>(DEFAULT_FILTERS);
  const [filters, setFilters] = useState<LedgerFiltersValue>(DEFAULT_FILTERS);
  const [offset, setOffset] = useState(0);
  const [limit] = useState(20);
  const [selectedRecord, setSelectedRecord] = useState<LedgerRecord | null>(null);
  const [view, setView] = useState<"table" | "timeline">("table");

  const normalizedFilters = useMemo(
    () => ({
      ...filters,
      agent: filters.agent || undefined,
      capability: filters.capability || undefined,
      status: filters.status || undefined,
      start_time: filters.start_time || undefined,
      end_time: filters.end_time || undefined,
    }),
    [filters]
  );

  const { data, isLoading, isError, error } = useLedger(normalizedFilters, limit, offset);

  if (isLoading) {
    return <PageShell title="Ledger" description="Loading ledger records..." />;
  }

  if (isError || !data) {
    return (
      <PageShell title="Ledger" description={`Failed to load ledger${error instanceof Error ? `: ${error.message}` : "."}`} />
    );
  }

  const applyFilters = () => {
    setOffset(0);
    setFilters(draftFilters);
  };

  const resetFilters = () => {
    setOffset(0);
    setDraftFilters(DEFAULT_FILTERS);
    setFilters(DEFAULT_FILTERS);
  };

  return (
    <PageShell title="Ledger" description="Execution audit records with query filters, pagination, and record detail.">
      <LedgerFilters value={draftFilters} onChange={setDraftFilters} onApply={applyFilters} onReset={resetFilters} />
      {data.items.length === 0 ? (
        <EmptyState
          title="No ledger records"
          description="Try broadening filters or trigger a run to generate audit records."
          actionLabel="Reset Filters"
          onAction={resetFilters}
        />
      ) : (
        <div className="grid gap-3 xl:grid-cols-[1.5fr_1fr]">
          <LedgerTimeline
            records={data.items}
            total={data.total}
            limit={data.limit}
            offset={data.offset}
            view={view}
            selectedId={selectedRecord?.id ?? null}
            onSelect={setSelectedRecord}
            onViewChange={setView}
            onPageChange={(nextOffset) => {
              setOffset(nextOffset);
              setSelectedRecord(null);
            }}
          />
          <LedgerDetail record={selectedRecord} />
        </div>
      )}
    </PageShell>
  );
}
