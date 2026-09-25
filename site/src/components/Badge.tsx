import { badgeLabels, type Badge as BadgeType } from "@/lib/projects";

const badgeVars: Record<BadgeType, React.CSSProperties> = {
  model: {
    color: "var(--badge-model-fg)",
    background: "var(--badge-model-bg)",
    boxShadow: "0 0 24px -8px var(--badge-model-glow)",
    borderColor: "color-mix(in oklab, var(--badge-model-fg) 35%, transparent)",
  },
  orchestration: {
    color: "var(--badge-orchestration-fg)",
    background: "var(--badge-orchestration-bg)",
    boxShadow: "0 0 24px -8px var(--badge-orchestration-glow)",
    borderColor: "color-mix(in oklab, var(--badge-orchestration-fg) 35%, transparent)",
  },
  infra: {
    color: "var(--badge-infra-fg)",
    background: "var(--badge-infra-bg)",
    boxShadow: "0 0 24px -8px var(--badge-infra-glow)",
    borderColor: "color-mix(in oklab, var(--badge-infra-fg) 35%, transparent)",
  },
  agent: {
    color: "var(--badge-agent-fg)",
    background: "var(--badge-agent-bg)",
    boxShadow: "0 0 24px -8px var(--badge-agent-glow)",
    borderColor: "color-mix(in oklab, var(--badge-agent-fg) 35%, transparent)",
  },
};

export function Badge({ type }: { type: BadgeType }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold tracking-wide"
      style={badgeVars[type]}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: "currentColor" }} />
      {badgeLabels[type]}
    </span>
  );
}
