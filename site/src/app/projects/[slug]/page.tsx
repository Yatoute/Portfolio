import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Badge } from "@/components/Badge";
import { MetricStat } from "@/components/MetricStat";
import { getProject, projects } from "@/lib/projects";

export function generateStaticParams() {
  return projects.map((p) => ({ slug: p.slug }));
}

export const dynamicParams = false;

async function loadCaseStudyBody(slug: string) {
  try {
    const mod = await import(`@/content/case-studies/${slug}.mdx`);
    return mod.default;
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const project = getProject(slug);
  if (!project) return {};
  return {
    title: `${project.title} — Yatoute MINTOAMA`,
    description: project.oneLiner,
  };
}

export default async function ProjectPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const project = getProject(slug);
  if (!project) notFound();

  const Body = await loadCaseStudyBody(slug);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-12 px-6 py-16 sm:py-24">
      <div className="animate-fade-up flex flex-col gap-5">
        <Badge type={project.badge} />
        <h1 className="text-4xl font-extrabold leading-tight tracking-tight sm:text-5xl">
          {project.title}
        </h1>
        <p className="text-xl leading-relaxed text-muted">{project.oneLiner}</p>
        <div className="flex flex-wrap gap-3 pt-2">
          {project.demoUrl && (
            <Link
              href={project.demoUrl}
              className="rounded-full px-5 py-2.5 text-sm font-bold text-background transition-transform hover:scale-105"
              style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
            >
              Tester la démo →
            </Link>
          )}
          {project.repoUrl && (
            <a
              href={project.repoUrl}
              className="card-glass rounded-full px-5 py-2.5 text-sm font-bold transition-colors hover:border-border-strong"
            >
              Voir le code
            </a>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {project.metrics.map((m) => (
          <MetricStat key={m.label} value={m.value} label={m.label} />
        ))}
      </div>

      <div className="flex flex-wrap gap-2 text-xs font-medium text-muted">
        {project.stack.map((s) => (
          <span key={s} className="rounded-full border border-border px-3 py-1">
            {s}
          </span>
        ))}
      </div>

      <article
        className="prose prose-invert max-w-none prose-headings:font-extrabold prose-headings:tracking-tight prose-a:font-semibold prose-a:no-underline prose-blockquote:rounded-2xl prose-blockquote:border-l-4 prose-blockquote:not-italic prose-strong:text-foreground prose-code:before:content-none prose-code:after:content-none"
        style={{
          // @ts-expect-error -- CSS custom properties for prose plugin theming
          "--tw-prose-body": "var(--muted)",
          "--tw-prose-headings": "var(--foreground)",
          "--tw-prose-links": "var(--accent)",
          "--tw-prose-bold": "var(--foreground)",
          "--tw-prose-quotes": "var(--foreground)",
          "--tw-prose-quote-borders": "var(--accent)",
          "--tw-prose-code": "var(--accent-2)",
          "--tw-prose-pre-bg": "var(--surface)",
          "--tw-prose-pre-code": "var(--foreground)",
        }}
      >
        {Body ? (
          <Body />
        ) : (
          <p>Contenu détaillé en cours de rédaction — revenez bientôt pour l&apos;étude de cas complète.</p>
        )}
      </article>
    </div>
  );
}
