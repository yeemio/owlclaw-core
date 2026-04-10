import { PageShell } from "@/pages/PageShell";

type ExternalDashboardPageProps = {
  kind: "traces" | "workflows";
};

const PAGE_META: Record<ExternalDashboardPageProps["kind"], { title: string; description: string }> = {
  traces: {
    title: "Traces",
    description: "Langfuse tracing dashboard links and health summary.",
  },
  workflows: {
    title: "Workflows",
    description: "Hatchet workflow dashboard links and health summary.",
  },
};

export function ExternalDashboardPage({ kind }: ExternalDashboardPageProps) {
  const links =
    kind === "traces"
      ? [
          { label: "Open Langfuse Dashboard", url: "http://localhost:3000" },
          { label: "Open Traces in New Tab", url: "http://localhost:3000/traces" },
        ]
      : [
          { label: "Open Hatchet Dashboard", url: "http://localhost:8080" },
          { label: "Open Workflows", url: "http://localhost:8080/workflows" },
        ];

  return (
    <PageShell title={PAGE_META[kind].title} description={PAGE_META[kind].description}>
      <section className="rounded-xl border border-border/70 bg-card/90 p-4">
        <h2 className="text-sm font-semibold">External Links</h2>
        <div className="mt-3 flex flex-wrap gap-2">
          {links.map((link) => (
            <a
              key={link.url}
              href={link.url}
              target="_blank"
              rel="noreferrer"
              className="rounded-md border border-border/70 bg-background/70 px-3 py-1.5 text-xs hover:border-primary/40"
            >
              {link.label}
            </a>
          ))}
        </div>
      </section>
    </PageShell>
  );
}
