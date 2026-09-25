import Link from "next/link";

export function HeroMetric({
  value,
  label,
  projectSlug,
  projectName,
}: {
  value: string;
  label: string;
  projectSlug: string;
  projectName: string;
}) {
  return (
    <Link
      href={`/projects/${projectSlug}`}
      className="card-glass glow-border group flex flex-col gap-1 rounded-2xl px-5 py-4 transition-transform hover:-translate-y-0.5"
    >
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
      <span
        className="mt-1 flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide opacity-70 transition-opacity group-hover:opacity-100"
        style={{ color: "var(--accent)" }}
      >
        {projectName} →
      </span>
    </Link>
  );
}
