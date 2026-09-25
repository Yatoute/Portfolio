import Link from "next/link";
import { Badge } from "./Badge";
import type { Project } from "@/lib/projects";

const statusLabels: Record<Project["status"], string> = {
  "live-demo": "● Démo en ligne",
  "case-study": "Étude de cas",
  "write-up": "Deep dive",
};

const glowByBadge: Record<Project["badge"], string> = {
  model: "var(--badge-model-fg)",
  orchestration: "var(--badge-orchestration-fg)",
  infra: "var(--badge-infra-fg)",
  agent: "var(--badge-agent-fg)",
};

export function ProjectCard({ project }: { project: Project }) {
  return (
    <Link
      href={`/projects/${project.slug}`}
      className="card-glass glow-border group relative flex flex-col gap-4 overflow-hidden rounded-3xl p-6 transition-transform duration-300 ease-out hover:-translate-y-1"
      style={{ "--glow-color": glowByBadge[project.badge] } as React.CSSProperties}
    >
      <div className="flex items-center justify-between gap-2">
        <Badge type={project.badge} />
        <span
          className="text-[11px] font-medium"
          style={{ color: project.status === "live-demo" ? "var(--badge-model-fg)" : "var(--muted)" }}
        >
          {statusLabels[project.status]}
        </span>
      </div>
      <h3 className="text-xl font-bold leading-snug tracking-tight text-foreground transition-colors group-hover:text-gradient">
        {project.title}
      </h3>
      <p className="text-sm leading-relaxed text-muted">{project.oneLiner}</p>
      <ul className="mt-auto flex flex-wrap gap-x-4 gap-y-1 border-t border-border pt-4 text-xs font-semibold text-foreground/80">
        {project.metrics.slice(0, 3).map((m) => (
          <li key={m.label} className="flex items-center gap-1.5">
            <span className="h-1 w-1 rounded-full bg-accent" />
            {m.value} {m.label}
          </li>
        ))}
      </ul>
    </Link>
  );
}
