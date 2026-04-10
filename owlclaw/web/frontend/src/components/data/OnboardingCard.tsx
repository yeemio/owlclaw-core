type LinkItem = {
  label: string;
  href: string;
};

const ONBOARDING_LINKS: LinkItem[] = [
  { label: "Quick Start", href: "https://github.com/owlclaw/owlclaw/blob/main/docs/QUICK_START.md" },
  { label: "Complete Workflow", href: "https://github.com/owlclaw/owlclaw/blob/main/docs/COMPLETE_WORKFLOW.md" },
  { label: "SKILL.md Guide", href: "https://github.com/owlclaw/owlclaw/blob/main/docs/SKILL.md" },
];

export function OnboardingCard() {
  return (
    <section className="rounded-xl border border-primary/30 bg-primary/10 p-4">
      <h2 className="text-sm font-semibold">First Run Guide</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        New to OwlClaw Console. Start from docs below to get runtime, governance, and skills online quickly.
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        {ONBOARDING_LINKS.map((link) => (
          <a
            key={link.href}
            href={link.href}
            target="_blank"
            rel="noreferrer"
            className="rounded-md border border-border/70 bg-background/80 px-3 py-2 text-xs hover:border-primary/50"
          >
            {link.label}
          </a>
        ))}
      </div>
    </section>
  );
}
