export function MetricStat({ value, label }: { value: string; label: string }) {
  return (
    <div className="card-glass glow-border flex flex-col gap-1 rounded-2xl px-5 py-4">
      <span
        className="break-words text-2xl leading-tight font-extrabold tracking-tight sm:text-3xl"
        style={{
          background: "linear-gradient(135deg, var(--accent), var(--accent-2))",
          backgroundClip: "text",
          WebkitBackgroundClip: "text",
          color: "transparent",
        }}
      >
        {value}
      </span>
      <span className="text-xs font-medium text-muted">{label}</span>
    </div>
  );
}
