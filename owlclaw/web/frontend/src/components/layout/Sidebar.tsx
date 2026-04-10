import { NavLink } from "react-router-dom";
import { cn } from "@/lib/utils";

type NavItem = {
  to: string;
  label: string;
};

const PRIMARY_ITEMS: NavItem[] = [
  { to: "/", label: "Overview" },
  { to: "/agents", label: "Agents" },
  { to: "/governance", label: "Governance" },
  { to: "/capabilities", label: "Capabilities" },
  { to: "/triggers", label: "Triggers" },
  { to: "/ledger", label: "Ledger" },
];

const EXTERNAL_ITEMS: NavItem[] = [
  { to: "/traces", label: "Traces" },
  { to: "/workflows", label: "Workflows" },
];

const SETTINGS_ITEMS: NavItem[] = [{ to: "/settings", label: "Settings" }];

function SidebarLink({ to, label }: NavItem) {
  return (
    <NavLink
      end={to === "/"}
      to={to}
      className={({ isActive }) =>
        cn(
          "block rounded-md border border-transparent px-3 py-2 text-sm transition-colors",
          "hover:border-primary/30 hover:bg-primary/10 hover:text-foreground",
          isActive && "border-primary/40 bg-primary/15 text-foreground"
        )
      }
    >
      {label}
    </NavLink>
  );
}

function SidebarSection({ title, items }: { title: string; items: NavItem[] }) {
  return (
    <section>
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        {title}
      </p>
      <div className="space-y-1">
        {items.map((item) => (
          <SidebarLink key={item.to} {...item} />
        ))}
      </div>
    </section>
  );
}

export function Sidebar() {
  return (
    <aside className="border-b border-border/70 bg-card/80 p-4 backdrop-blur lg:border-b-0 lg:border-r">
      <div className="mb-6">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-primary">OwlClaw</p>
        <h1 className="mt-1 text-xl font-semibold tracking-tight">Console</h1>
      </div>
      <div className="space-y-5">
        <SidebarSection title="Core" items={PRIMARY_ITEMS} />
        <SidebarSection title="External" items={EXTERNAL_ITEMS} />
        <SidebarSection title="System" items={SETTINGS_ITEMS} />
      </div>
    </aside>
  );
}
