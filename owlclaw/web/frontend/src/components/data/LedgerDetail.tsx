import { type LedgerRecord } from "@/hooks/useApi";

type LedgerDetailProps = {
  record: LedgerRecord | null;
};

function DetailBlock({ title, content }: { title: string; content: string }) {
  return (
    <div className="space-y-1">
      <h3 className="text-xs uppercase tracking-[0.12em] text-muted-foreground">{title}</h3>
      <pre className="max-h-44 overflow-auto rounded-md border border-border/60 bg-background/70 p-3 text-xs whitespace-pre-wrap">
        {content || "-"}
      </pre>
    </div>
  );
}

export function LedgerDetail({ record }: LedgerDetailProps) {
  if (!record) {
    return (
      <section className="rounded-xl border border-border/70 bg-card/90 p-4">
        <h2 className="text-sm font-semibold">Execution Detail</h2>
        <p className="mt-2 text-sm text-muted-foreground">Select a record from the table or timeline.</p>
      </section>
    );
  }

  return (
    <section className="rounded-xl border border-border/70 bg-card/90 p-4">
      <h2 className="text-sm font-semibold">Execution Detail</h2>
      <div className="mt-2 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
        <p>Record: {record.id}</p>
        <p>Agent: {record.agent}</p>
        <p>Capability: {record.capability}</p>
        <p>Model: {record.model}</p>
        <p>Latency: {record.latency_ms.toFixed(0)} ms</p>
        <p>Cost: ${record.cost_usd.toFixed(4)}</p>
      </div>
      <div className="mt-4 space-y-3">
        <DetailBlock title="Input" content={record.input} />
        <DetailBlock title="Output" content={record.output} />
        <DetailBlock title="Decision Reasoning" content={record.reasoning} />
      </div>
    </section>
  );
}
