import { useSettings } from "@/hooks/useApi";
import { PageShell } from "@/pages/PageShell";

export function SettingsPage() {
  const { data, isLoading, isError, error } = useSettings();

  if (isLoading) {
    return <PageShell title="Settings" description="Loading settings..." />;
  }

  if (isError || !data) {
    return (
      <PageShell title="Settings" description={`Failed to load settings${error instanceof Error ? `: ${error.message}` : "."}`} />
    );
  }

  return (
    <PageShell title="Settings" description="Runtime configuration, platform status, version provenance, and docs links.">
      <div className="grid gap-3 xl:grid-cols-2">
        <section className="rounded-xl border border-border/70 bg-card/90 p-4">
          <h2 className="text-sm font-semibold">Runtime Config (Read-only)</h2>
          <pre className="mt-3 max-h-72 overflow-auto rounded-md border border-border/60 bg-background/70 p-3 text-xs">
            {JSON.stringify(data.config, null, 2)}
          </pre>
        </section>

        <section className="space-y-3">
          <div className="rounded-xl border border-border/70 bg-card/90 p-4">
            <h2 className="text-sm font-semibold">MCP Status</h2>
            <p className="mt-2 text-xs text-muted-foreground">Server: {data.mcp_status.server_up ? "up" : "down"}</p>
            <p className="text-xs text-muted-foreground">Connected clients: {data.mcp_status.connected_clients}</p>
          </div>
          <div className="rounded-xl border border-border/70 bg-card/90 p-4">
            <h2 className="text-sm font-semibold">Database</h2>
            <p className="mt-2 text-xs text-muted-foreground">Healthy: {data.db_status.healthy ? "yes" : "no"}</p>
            <p className="text-xs text-muted-foreground">Migration version: {data.db_status.migration_version}</p>
          </div>
          <div className="rounded-xl border border-border/70 bg-card/90 p-4">
            <h2 className="text-sm font-semibold">Version & Provenance</h2>
            <p className="mt-2 text-xs text-muted-foreground">Version: {data.version.app_version}</p>
            <p className="text-xs text-muted-foreground">Build: {data.version.build_time}</p>
            <p className="text-xs text-muted-foreground">Commit: {data.version.commit_hash}</p>
            <p className="text-xs text-muted-foreground">Provenance: {data.version.provenance}</p>
          </div>
        </section>
      </div>

      <section className="rounded-xl border border-border/70 bg-card/90 p-4">
        <h2 className="text-sm font-semibold">Developer Docs</h2>
        <div className="mt-3 flex flex-wrap gap-2">
          {data.docs.map((doc) => (
            <a
              key={doc.url}
              href={doc.url}
              target="_blank"
              rel="noreferrer"
              className="rounded-md border border-border/70 bg-background/70 px-3 py-1.5 text-xs hover:border-primary/40"
            >
              {doc.title}
            </a>
          ))}
        </div>
      </section>
    </PageShell>
  );
}
