type EmptyStateProps = {
  title: string;
  description: string;
  actionLabel?: string;
  onAction?: () => void;
};

export function EmptyState({ title, description, actionLabel, onAction }: EmptyStateProps) {
  return (
    <section className="rounded-xl border border-dashed border-border/80 bg-background/60 p-6 text-center">
      <div className="mx-auto mb-3 h-10 w-10 rounded-full border border-border/70 bg-card/80" />
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="mt-2 text-xs text-muted-foreground">{description}</p>
      {actionLabel && onAction && (
        <button
          type="button"
          onClick={onAction}
          className="mt-4 rounded-md border border-primary/50 bg-primary/15 px-3 py-1.5 text-xs"
        >
          {actionLabel}
        </button>
      )}
    </section>
  );
}
