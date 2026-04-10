import { type VisibilityRow } from "@/hooks/useApi";

type VisibilityMatrixProps = {
  rows: VisibilityRow[];
};

export function VisibilityMatrix({ rows }: VisibilityMatrixProps) {
  const capabilities = Array.from(
    new Set(rows.flatMap((row) => Object.keys(row.capabilities)))
  );

  return (
    <section className="rounded-xl border border-border/70 bg-card/90 p-4">
      <h2 className="text-sm font-semibold">Capability Visibility Matrix</h2>
      <div className="mt-4 overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-1 text-xs">
          <thead>
            <tr>
              <th className="px-2 py-1 text-left text-muted-foreground">Agent</th>
              {capabilities.map((capability) => (
                <th key={capability} className="px-2 py-1 text-left text-muted-foreground">
                  {capability}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.agent}>
                <td className="rounded bg-background/80 px-2 py-1 font-medium">{row.agent}</td>
                {capabilities.map((capability) => {
                  const visible = row.capabilities[capability] ?? false;
                  return (
                    <td
                      key={`${row.agent}-${capability}`}
                      className={`rounded px-2 py-1 ${visible ? "bg-emerald-500/25 text-emerald-200" : "bg-muted/40 text-muted-foreground"}`}
                    >
                      {visible ? "Visible" : "Hidden"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
