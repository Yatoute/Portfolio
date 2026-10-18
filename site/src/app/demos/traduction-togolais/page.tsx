"use client";

import { useState } from "react";
import Link from "next/link";
import { callHfSpace } from "@/lib/hfCall";

const LANGUAGES = ["Français", "Éwé", "Kabiyè", "Moba"];

async function callTranslate(text: string, src: string, tgt: string): Promise<string> {
  const data = await callHfSpace("traduction-togolais", [text, src, tgt]);
  return data[0] as string;
}

export default function TraductionTogolaisDemo() {
  const [text, setText] = useState("");
  const [src, setSrc] = useState("Français");
  const [tgt, setTgt] = useState("Moba");
  const [result, setResult] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (!text.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await callTranslate(text, src, tgt);
      setResult(r);
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : "La démo était peut-être en train de démarrer (GPU alloué à la demande) — réessayez dans quelques secondes."
      );
    } finally {
      setLoading(false);
    }
  }

  function swapLanguages() {
    setSrc(tgt);
    setTgt(src);
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-10 px-6 py-16 sm:py-24">
      <div className="animate-fade-up flex flex-col gap-4">
        <Link
          href="/projects/traduction-langues-togolaises"
          className="text-sm font-semibold hover:underline"
          style={{ color: "var(--accent)" }}
        >
          ← Traduction langues togolaises
        </Link>
        <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl">
          Traduction <span className="text-gradient">éwé / kabiyè / moba</span> ↔ français
        </h1>
        <p className="text-lg text-muted">
          NLLB-200 (600M) fine-tuné — un bug de perte d&apos;accents français en sortie
          diagnostiqué puis corrigé (95% des traductions kabiyè→français affectées).
        </p>
      </div>

      <div className="card-glass glow-border flex flex-col gap-4 rounded-3xl p-7">
        <div className="flex items-center gap-3">
          <select
            value={src}
            onChange={(e) => setSrc(e.target.value)}
            className="rounded-xl border border-border bg-background-2 p-2 text-sm"
          >
            {LANGUAGES.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
          <button
            onClick={swapLanguages}
            className="rounded-full border border-border px-3 py-1.5 text-sm text-muted hover:border-border-strong"
            aria-label="Inverser les langues"
          >
            ⇄
          </button>
          <select
            value={tgt}
            onChange={(e) => setTgt(e.target.value)}
            className="rounded-xl border border-border bg-background-2 p-2 text-sm"
          >
            {LANGUAGES.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </div>

        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Texte à traduire..."
          rows={4}
          maxLength={1000}
          className="rounded-xl border border-border bg-background-2 p-4 text-sm outline-none focus:border-border-strong"
        />
        <button
          onClick={handleSubmit}
          disabled={loading || !text.trim()}
          className="w-fit rounded-full px-6 py-2.5 text-sm font-bold text-background transition-transform hover:scale-105 disabled:opacity-50 disabled:hover:scale-100"
          style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
        >
          {loading ? "Traduction en cours..." : "Traduire"}
        </button>

        {error && <div className="rounded-xl border border-border bg-background-2 p-4 text-sm text-muted">{error}</div>}

        {result !== null && (
          <div className="flex flex-col gap-2 border-t border-border pt-5">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted">Traduction</span>
            <p className="rounded-xl border border-border bg-background-2 p-4 text-sm">{result}</p>
          </div>
        )}
      </div>
    </div>
  );
}
