type SchemaViewerProps = {
  schema: Record<string, unknown>;
};

export function SchemaViewer({ schema }: SchemaViewerProps) {
  return (
    <div className="rounded-md border border-border/60 bg-background/70 p-3">
      <pre className="max-h-72 overflow-auto text-xs leading-relaxed">
        {JSON.stringify(schema, null, 2)}
      </pre>
    </div>
  );
}
