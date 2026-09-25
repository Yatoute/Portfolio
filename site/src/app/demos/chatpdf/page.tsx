"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { CHATPDF_API } from "@/lib/demoConfig";

type PageRef = number | string;
type Source = { file_name: string; pages: PageRef[] };
type Message = { role: "user" | "assistant"; content: string; sources?: Source[] };
type CitationEntry = { label: string; page: number };

const MAX_UPLOAD_MB = 20;

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${CHATPDF_API}${path}`, init);
  if (res.status === 429) throw new Error("RATE_LIMIT");
  if (res.status === 404) throw new Error("NOT_FOUND");
  if (!res.ok) throw new Error(`Erreur API (${res.status})`);
  return res.json();
}

// Mirrors the original Streamlit UI's page-reference parsing: citations can be
// a single page (5) or a range ("15-20") — normalize to unique, sorted entries.
function extractCitationEntries(sources: Source[] | undefined): CitationEntry[] {
  if (!sources) return [];
  const seen = new Set<string>();
  const entries: CitationEntry[] = [];
  for (const s of sources) {
    for (const raw of s.pages || []) {
      let label: string | null = null;
      let page: number | null = null;
      if (typeof raw === "number" && raw > 0) {
        label = String(raw);
        page = raw;
      } else if (typeof raw === "string") {
        const trimmed = raw.trim();
        if (/^\d+$/.test(trimmed)) {
          const p = parseInt(trimmed, 10);
          if (p > 0) {
            label = trimmed;
            page = p;
          }
        } else if (trimmed.includes("-")) {
          const [a, b] = trimmed.split("-", 2).map((p) => p.trim());
          if (/^\d+$/.test(a) && /^\d+$/.test(b) && parseInt(b, 10) >= parseInt(a, 10)) {
            label = `${a}-${b}`;
            page = parseInt(a, 10);
          }
        }
      }
      if (label && page && !seen.has(label)) {
        seen.add(label);
        entries.push({ label, page });
      }
    }
  }
  return entries.sort((a, b) => a.page - b.page);
}

// pdfjs-dist touches browser-only APIs (Worker, DOMMatrix) — load lazily,
// client-side only, never at module scope (would break the SSR pass).
let pdfjsPromise: Promise<typeof import("pdfjs-dist")> | null = null;
function getPdfJs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import("pdfjs-dist").then((mod) => {
      mod.GlobalWorkerOptions.workerSrc = new URL(
        "pdfjs-dist/build/pdf.worker.min.mjs",
        import.meta.url
      ).toString();
      return mod;
    });
  }
  return pdfjsPromise;
}

function PdfPageViewer({
  pdfBytes,
  page,
  totalPages,
  onPageChange,
  showGoTo = false,
}: {
  pdfBytes: ArrayBuffer;
  page: number;
  totalPages: number;
  onPageChange: (p: number) => void;
  showGoTo?: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pdfDocRef = useRef<import("pdfjs-dist").PDFDocumentProxy | null>(null);
  const [rendering, setRendering] = useState(true);
  const [gotoValue, setGotoValue] = useState(String(page));

  useEffect(() => {
    setGotoValue(String(page));
  }, [page]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setRendering(true);
      const pdfjsLib = await getPdfJs();
      if (cancelled) return;
      if (!pdfDocRef.current) {
        // pdfjs detaches/transfers the buffer — copy it so the caller's cached bytes stay usable.
        const doc = await pdfjsLib.getDocument({ data: pdfBytes.slice(0) }).promise;
        if (cancelled) return;
        pdfDocRef.current = doc;
      }
      const pdfPage = await pdfDocRef.current.getPage(page);
      if (cancelled) return;
      const viewport = pdfPage.getViewport({ scale: 1.6 });
      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      await pdfPage.render({ canvasContext: ctx, viewport, canvas }).promise;
      if (!cancelled) setRendering(false);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pdfBytes, page]);

  useEffect(() => {
    return () => {
      (pdfDocRef.current as unknown as { destroy?: () => void } | null)?.destroy?.();
    };
  }, []);

  return (
    <div className="flex flex-col gap-3">
      {showGoTo && (
        <div className="flex items-center justify-center gap-2 text-sm">
          <span className="text-muted">Aller à la page</span>
          <input
            type="number"
            min={1}
            max={totalPages}
            value={gotoValue}
            onChange={(e) => setGotoValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                const p = Math.max(1, Math.min(totalPages, parseInt(gotoValue, 10) || 1));
                onPageChange(p);
              }
            }}
            className="w-16 rounded-lg border border-border bg-background-2 px-2 py-1 text-center outline-none focus:border-border-strong"
          />
          <span className="text-muted">/ {totalPages}</span>
        </div>
      )}
      <div className="relative overflow-auto rounded-xl border border-border bg-background-2">
        {rendering && (
          <div className="flex h-40 items-center justify-center text-sm text-muted">Rendu de la page...</div>
        )}
        <canvas ref={canvasRef} className={`mx-auto max-w-full ${rendering ? "hidden" : "block"}`} />
      </div>
      <div className="flex items-center justify-center gap-3">
        <button
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          className="rounded-full border border-border px-3 py-1 text-xs font-semibold transition-colors hover:border-border-strong disabled:opacity-40"
        >
          ◀ Précédent
        </button>
        <span className="text-xs text-muted">Page {page} / {totalPages}</span>
        <button
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          disabled={page >= totalPages}
          className="rounded-full border border-border px-3 py-1 text-xs font-semibold transition-colors hover:border-border-strong disabled:opacity-40"
        >
          Suivant ▶
        </button>
      </div>
    </div>
  );
}

export default function ChatPdfDemo() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [indexId, setIndexId] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [totalPages, setTotalPages] = useState<number | null>(null);

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [toolCaptions, setToolCaptions] = useState<string[]>([]);

  const [booting, setBooting] = useState(true);
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [view, setView] = useState<"chat" | "document">("chat");
  const [showUploadOptions, setShowUploadOptions] = useState(false);
  const [extractImages, setExtractImages] = useState(true);
  const [limitPages, setLimitPages] = useState(false);
  const [startPage, setStartPage] = useState(1);
  const [maxPages, setMaxPages] = useState(10);

  const [pdfBytes, setPdfBytes] = useState<ArrayBuffer | null>(null);
  const [loadingPdf, setLoadingPdf] = useState(false);
  const [docPage, setDocPage] = useState(1);
  const [citationPanels, setCitationPanels] = useState<
    Record<number, { entries: CitationEntry[]; page: number }>
  >({});

  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const indexIdRef = useRef<string | null>(null);
  indexIdRef.current = indexId;

  async function startSessionFromIndex(idxId: string, label: string, pages?: number | null) {
    const session = await api<{ session_id: string }>("/api/v1/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index_id: idxId }),
    });
    setSessionId(session.session_id);
    setIndexId(idxId);
    setFileName(label);
    setTotalPages(pages ?? null);
    setMessages([]);
    setPdfBytes(null);
    setDocPage(1);
    setCitationPanels({});
  }

  useEffect(() => {
    (async () => {
      try {
        const { index_id } = await api<{ index_id: string }>("/api/v1/default-index");
        await startSessionFromIndex(index_id, "Pre-trained Models for NLP.pdf (préchargé)");
      } catch {
        setError("Impossible de charger le document par défaut — la démo est peut-être en veille, réessayez dans une minute.");
      } finally {
        setBooting(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keeps the current index alive against the backend's 20-minute no-heartbeat
  // watchdog cleanup while this tab stays open on a document.
  useEffect(() => {
    if (!indexId) return;
    const tick = () => {
      fetch(`${CHATPDF_API}/api/v1/heartbeat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index_id: indexIdRef.current }),
      }).catch(() => {});
    };
    tick();
    const interval = setInterval(tick, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [indexId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, toolCaptions]);

  async function ensurePdfBytes(): Promise<ArrayBuffer | null> {
    if (pdfBytes) return pdfBytes;
    if (!indexId) return null;
    setLoadingPdf(true);
    try {
      const res = await fetch(`${CHATPDF_API}/api/v1/indexes/${indexId}/pdf`);
      if (!res.ok) throw new Error(`Erreur API (${res.status})`);
      const buf = await res.arrayBuffer();
      setPdfBytes(buf);
      if (!totalPages) {
        const pdfjsLib = await getPdfJs();
        const doc = await pdfjsLib.getDocument({ data: buf.slice(0) }).promise;
        setTotalPages(doc.numPages);
        (doc as unknown as { destroy?: () => void }).destroy?.();
      }
      return buf;
    } catch {
      setError("Impossible de charger l'aperçu du PDF.");
      return null;
    } finally {
      setLoadingPdf(false);
    }
  }

  async function openDocumentView() {
    setView("document");
    await ensurePdfBytes();
  }

  async function toggleCitations(messageIndex: number, sources: Source[] | undefined) {
    if (citationPanels[messageIndex]) {
      setCitationPanels((p) => {
        const next = { ...p };
        delete next[messageIndex];
        return next;
      });
      return;
    }
    const entries = extractCitationEntries(sources);
    if (entries.length === 0) return;
    await ensurePdfBytes();
    setCitationPanels((p) => ({ ...p, [messageIndex]: { entries, page: entries[0].page } }));
  }

  async function streamMessage(sid: string, text: string): Promise<{ content: string; sources: Source[] }> {
    const res = await fetch(`${CHATPDF_API}/api/v1/sessions/${sid}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    if (res.status === 404) throw new Error("NOT_FOUND");
    if (res.status === 429) throw new Error("RATE_LIMIT");
    if (!res.ok || !res.body) throw new Error(`Erreur API (${res.status})`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result: { content: string; sources: Source[] } | null = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() ?? "";
      for (const chunk of chunks) {
        const line = chunk.trim();
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (data === "[DONE]") continue;
        const event = JSON.parse(data);
        if (event.type === "tool") {
          setToolCaptions((prev) => [...prev, event.content]);
        } else if (event.type === "result") {
          result = { content: event.payload.content, sources: event.payload.sources ?? [] };
        }
      }
    }
    if (!result) throw new Error("Réponse vide.");
    return result;
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || !sessionId || sending) return;
    setInput("");
    setError(null);
    setMessages((m) => [...m, { role: "user", content: text }]);
    setSending(true);
    setToolCaptions([]);
    try {
      let sid = sessionId;
      let result;
      try {
        result = await streamMessage(sid, text);
      } catch (e) {
        if (e instanceof Error && e.message === "NOT_FOUND" && indexIdRef.current) {
          // Session lost server-side (e.g. a backend restart) — silently
          // recreate it against the same document and retry once.
          const session = await api<{ session_id: string }>("/api/v1/sessions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ index_id: indexIdRef.current }),
          });
          sid = session.session_id;
          setSessionId(sid);
          setToolCaptions([]);
          result = await streamMessage(sid, text);
        } else {
          throw e;
        }
      }
      setMessages((m) => [...m, { role: "assistant", content: result.content, sources: result.sources }]);
    } catch (e) {
      const msg =
        e instanceof Error && e.message === "RATE_LIMIT"
          ? "Limite de messages atteinte pour cette démo — réessayez plus tard."
          : "La question n'a pas pu être traitée.";
      setError(msg);
    } finally {
      setToolCaptions([]);
      setSending(false);
    }
  }

  async function handleUpload(file: File) {
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setError(`Fichier trop volumineux — limite de ${MAX_UPLOAD_MB}MB pour cette démo.`);
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("extract_images", String(extractImages));
      if (limitPages) {
        form.append("start_page", String(startPage));
        form.append("max_pages", String(maxPages));
      }
      const res = await api<{ index_id: string; file_name: string; total_pages: number | null }>(
        "/api/v1/documents",
        { method: "POST", body: form }
      );
      await startSessionFromIndex(res.index_id, res.file_name, res.total_pages);
      setShowUploadOptions(false);
    } catch (e) {
      const msg =
        e instanceof Error && e.message === "RATE_LIMIT"
          ? "Limite d'uploads atteinte pour cette démo — réessayez plus tard."
          : "L'upload ou l'indexation a échoué (uniquement des PDF).";
      setError(msg);
    } finally {
      setUploading(false);
    }
  }

  async function handleReset() {
    if (!sessionId || !indexId || sending) return;
    setError(null);
    try {
      await fetch(`${CHATPDF_API}/api/v1/sessions/${sessionId}`, { method: "DELETE" });
    } catch {
      // best-effort
    }
    try {
      await startSessionFromIndex(indexId, fileName ?? "document.pdf", totalPages);
    } catch {
      setError("Impossible de réinitialiser la conversation.");
    }
  }

  const busy = booting || uploading;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-16 sm:py-24">
      <div className="animate-fade-up flex flex-col gap-4">
        <Link href="/projects/chatpdf" className="text-sm font-semibold hover:underline" style={{ color: "var(--accent)" }}>
          ← ChatPDF
        </Link>
        <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl">
          Chattez avec un <span className="text-gradient">PDF</span>
        </h1>
        <p className="text-lg text-muted">
          Agent LangGraph avec recherche sémantique, navigation entre chunks et lecture de page —
          un document est déjà préchargé, ou uploadez le vôtre.
        </p>
      </div>

      <div className="card-glass flex flex-col gap-3 rounded-2xl px-5 py-4 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="truncate text-muted">
            {booting ? "Chargement..." : fileName ? `📄 ${fileName}` : "Aucun document"}
            {totalPages ? ` · ${totalPages} pages` : ""}
          </span>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => setShowUploadOptions((v) => !v)}
              disabled={busy}
              className="shrink-0 rounded-full border border-border px-3 py-1 text-xs font-semibold transition-colors hover:border-border-strong disabled:opacity-50"
            >
              {uploading ? "Envoi..." : "Uploader un PDF"}
            </button>
            <button
              onClick={handleReset}
              disabled={busy || sending || !sessionId}
              className="shrink-0 rounded-full border border-border px-3 py-1 text-xs font-semibold transition-colors hover:border-border-strong disabled:opacity-50"
            >
              Réinitialiser la conversation
            </button>
          </div>
        </div>

        {showUploadOptions && (
          <div className="flex flex-col gap-3 rounded-xl border border-border bg-background-2 p-4">
            <label className="flex items-center justify-between gap-3 text-xs">
              <span>Extraire les images (OCR) — désactivez pour un gros PDF scanné</span>
              <input type="checkbox" checked={extractImages} onChange={(e) => setExtractImages(e.target.checked)} />
            </label>
            <label className="flex items-center justify-between gap-3 text-xs">
              <span>Limiter les pages indexées</span>
              <input type="checkbox" checked={limitPages} onChange={(e) => setLimitPages(e.target.checked)} />
            </label>
            {limitPages && (
              <div className="flex gap-3 text-xs">
                <label className="flex items-center gap-2">
                  Page de départ
                  <input
                    type="number"
                    min={1}
                    value={startPage}
                    onChange={(e) => setStartPage(parseInt(e.target.value, 10) || 1)}
                    className="w-16 rounded-lg border border-border bg-background px-2 py-1 outline-none"
                  />
                </label>
                <label className="flex items-center gap-2">
                  Nombre de pages max
                  <input
                    type="number"
                    min={1}
                    value={maxPages}
                    onChange={(e) => setMaxPages(parseInt(e.target.value, 10) || 1)}
                    className="w-16 rounded-lg border border-border bg-background px-2 py-1 outline-none"
                  />
                </label>
              </div>
            )}
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="self-start rounded-full px-4 py-1.5 text-xs font-bold text-background transition-transform hover:scale-105 disabled:opacity-50"
              style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
            >
              Choisir un fichier PDF (≤{MAX_UPLOAD_MB}MB)
            </button>
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleUpload(f);
            e.target.value = "";
          }}
        />
      </div>

      <div className="flex gap-2 text-sm">
        <button
          onClick={() => setView("chat")}
          className={`rounded-full px-4 py-1.5 font-semibold transition-colors ${
            view === "chat" ? "text-background" : "card-glass text-muted hover:text-foreground"
          }`}
          style={view === "chat" ? { background: "linear-gradient(135deg, var(--accent), var(--accent-2))" } : undefined}
        >
          Chat
        </button>
        <button
          onClick={openDocumentView}
          disabled={!indexId}
          className={`rounded-full px-4 py-1.5 font-semibold transition-colors disabled:opacity-50 ${
            view === "document" ? "text-background" : "card-glass text-muted hover:text-foreground"
          }`}
          style={view === "document" ? { background: "linear-gradient(135deg, var(--accent), var(--accent-2))" } : undefined}
        >
          Document
        </button>
      </div>

      {view === "chat" ? (
        <div className="card-glass glow-border flex flex-col gap-4 rounded-3xl p-6">
          <div ref={scrollRef} className="flex max-h-[55vh] min-h-[240px] flex-col gap-4 overflow-y-auto pr-1">
            {messages.length === 0 && !booting && (
              <p className="text-sm text-muted">Posez une question sur le document ci-dessus.</p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`flex flex-col gap-1 ${m.role === "user" ? "items-end" : "items-start"}`}>
                <div
                  className="max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed"
                  style={
                    m.role === "user"
                      ? { background: "linear-gradient(135deg, var(--accent), var(--accent-2))", color: "var(--background)" }
                      : { background: "var(--surface-2)", color: "var(--foreground)" }
                  }
                >
                  {m.content}
                </div>
                {m.role === "assistant" && m.sources && m.sources.length > 0 && (
                  <div className="flex w-full max-w-[85%] flex-col gap-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-[11px] text-muted">
                        {Array.from(new Set(m.sources.map((s) => s.file_name))).join(", ")}
                      </span>
                      <button
                        onClick={() => toggleCitations(i, m.sources)}
                        className="rounded-full border border-border px-2 py-0.5 text-[11px] font-semibold text-muted transition-colors hover:border-border-strong hover:text-foreground"
                      >
                        {citationPanels[i] ? "Masquer les pages citées" : "Voir les pages citées"}
                      </button>
                    </div>
                    {citationPanels[i] && (
                      <div className="rounded-2xl border border-border bg-background-2 p-3">
                        <div className="flex flex-wrap gap-1.5 pb-3">
                          {citationPanels[i].entries.map((entry) => (
                            <button
                              key={entry.label}
                              onClick={() =>
                                setCitationPanels((p) => ({ ...p, [i]: { ...p[i], page: entry.page } }))
                              }
                              className="rounded-full border border-border px-2.5 py-1 text-[11px] font-semibold transition-colors hover:border-border-strong"
                              style={
                                citationPanels[i].page === entry.page
                                  ? { background: "var(--accent)", color: "var(--background)", borderColor: "var(--accent)" }
                                  : undefined
                              }
                            >
                              Page {entry.label}
                            </button>
                          ))}
                        </div>
                        {pdfBytes && totalPages ? (
                          <PdfPageViewer
                            pdfBytes={pdfBytes}
                            page={citationPanels[i].page}
                            totalPages={totalPages}
                            onPageChange={(p) => setCitationPanels((prev) => ({ ...prev, [i]: { ...prev[i], page: p } }))}
                          />
                        ) : (
                          <p className="text-xs text-muted">{loadingPdf ? "Chargement de l'aperçu..." : "Aperçu indisponible."}</p>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            {sending && (
              <div className="flex flex-col gap-1.5">
                {toolCaptions.map((c, idx) => (
                  <p key={idx} className="text-xs text-muted">{c}</p>
                ))}
                <div className="flex items-center gap-2 text-sm text-muted">
                  <span className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  L&apos;agent réfléchit...
                </div>
              </div>
            )}
          </div>

          {error && <div className="rounded-xl border border-border bg-background-2 p-3 text-sm text-muted">{error}</div>}

          <div className="flex gap-2 border-t border-border pt-4">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              placeholder={booting ? "Chargement du document..." : "Posez une question..."}
              disabled={busy || !sessionId}
              className="flex-1 rounded-full border border-border bg-background-2 px-4 py-2.5 text-sm outline-none focus:border-border-strong disabled:opacity-50"
            />
            <button
              onClick={handleSend}
              disabled={busy || !sessionId || sending || !input.trim()}
              className="rounded-full px-5 py-2.5 text-sm font-bold text-background transition-transform hover:scale-105 disabled:opacity-50 disabled:hover:scale-100"
              style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
            >
              Envoyer
            </button>
          </div>
        </div>
      ) : (
        <div className="card-glass glow-border rounded-3xl p-6">
          {pdfBytes && totalPages ? (
            <PdfPageViewer pdfBytes={pdfBytes} page={docPage} totalPages={totalPages} onPageChange={setDocPage} showGoTo />
          ) : (
            <p className="py-10 text-center text-sm text-muted">
              {loadingPdf ? "Chargement du document..." : "Document indisponible."}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
