"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/projects", label: "Projets" },
  { href: "/cv", label: "CV" },
];

const activePillClass =
  "rounded-full px-4 py-1.5 text-sm font-semibold text-background transition-transform hover:scale-105";
const inactiveClass = "font-medium text-muted transition-colors hover:text-foreground";

export function SiteHeader() {
  const pathname = usePathname();

  const activeHref = links.find(
    (l) => pathname === l.href || pathname.startsWith(`${l.href}/`)
  )?.href;

  return (
    <header
      className="sticky top-0 z-20 border-b border-border"
      style={{ background: "color-mix(in oklab, var(--background) 75%, transparent)", backdropFilter: "blur(16px)" }}
    >
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <Link href="/" className="font-bold tracking-tight text-foreground">
          Yatoute MINTOAMA<span style={{ color: "var(--accent)" }}>.</span>
        </Link>
        <nav className="flex items-center gap-6 text-sm">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={l.href === activeHref ? activePillClass : inactiveClass}
              style={l.href === activeHref ? { background: "linear-gradient(135deg, var(--accent), var(--accent-2))" } : undefined}
            >
              {l.label}
            </Link>
          ))}
          <a
            href="mailto:mintoamayatoute@gmail.com"
            className={activeHref ? inactiveClass : activePillClass}
            style={activeHref ? undefined : { background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
          >
            Contact
          </a>
        </nav>
      </div>
    </header>
  );
}
