import { useLocation } from "react-router-dom";

const TITLES: Record<string, string> = {
  "/": "Overview",
  "/agents": "Agents",
  "/governance": "Governance",
  "/capabilities": "Capabilities",
  "/triggers": "Triggers",
  "/ledger": "Ledger",
  "/traces": "Traces",
  "/workflows": "Workflows",
  "/settings": "Settings",
};

export function Header() {
  const location = useLocation();
  const title = TITLES[location.pathname] ?? "Console";

  return (
    <header className="border-b border-border/70 bg-background/90 px-6 py-4 backdrop-blur lg:px-8">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Control Plane</p>
          <h2 className="mt-1 text-lg font-semibold tracking-tight">{title}</h2>
        </div>
        <div className="rounded-md border border-primary/40 bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
          Dark Theme
        </div>
      </div>
    </header>
  );
}
