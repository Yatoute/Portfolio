export function SiteFooter() {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto flex max-w-6xl flex-col gap-2 px-6 py-8 text-sm text-muted sm:flex-row sm:items-center sm:justify-between">
        <span>© {new Date().getFullYear()} Yatoute MINTOAMA</span>
        <div className="flex gap-4">
          <a href="https://github.com/Yatoute" className="transition-colors hover:text-foreground">
            GitHub
          </a>
          <a href="https://www.linkedin.com/in/yatoute/" className="transition-colors hover:text-foreground">
            LinkedIn
          </a>
          <a href="mailto:mintoamayatoute@gmail.com" className="transition-colors hover:text-foreground">
            Email
          </a>
        </div>
      </div>
    </footer>
  );
}
