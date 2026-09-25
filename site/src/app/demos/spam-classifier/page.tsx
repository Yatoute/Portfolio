"use client";

import { useState } from "react";
import Link from "next/link";
import { GPTLIGHT_API } from "@/lib/demoConfig";

const EXAMPLES = [
  "Congratulations! You have WON a $1000 gift card. Click here to claim now!!!",
  "Hey, are we still on for lunch tomorrow at noon?",
  "URGENT: Your account has been suspended. Verify your identity immediately by clicking this link.",
  "FREE entry in a weekly competition to win an iPad. Text WIN to 80086 now!",
];

type Result = { ham: number; spam: number };

async function callClassify(text: string): Promise<Result> {
  const res = await fetch(`${GPTLIGHT_API}/gradio_api/call/classify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data: [text] }),
  });
  if (!res.ok) throw new Error("Appel API échoué.");
  const { event_id } = await res.json();

  const streamRes = await fetch(`${GPTLIGHT_API}/gradio_api/call/classify/${event_id}`);
  const raw = await streamRes.text();
  const dataLine = raw.split("\n").find((l) => l.startsWith("data:"));
  if (!dataLine) throw new Error("Réponse inattendue du modèle.");
  const parsed = JSON.parse(dataLine.slice("data:".length).trim());
  const confidences: { label: string; confidence: number }[] = parsed[0].confidences;
  const byLabel = Object.fromEntries(confidences.map((c) => [c.label, c.confidence]));
  return { ham: byLabel.ham ?? 0, spam: byLabel.spam ?? 0 };
}

export default function SpamClassifierDemo() {
  const [text, setText] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(inputText: string) {
    if (!inputText.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await callClassify(inputText);
      setResult(r);
    } catch {
      setError(
        "La démo était peut-être en veille (elle scale à zéro entre deux visites) — réessayez dans quelques secondes."
      );
    } finally {
      setLoading(false);
    }
  }

  const prediction = result ? (result.spam > result.ham ? "spam" : "ham") : null;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-10 px-6 py-16 sm:py-24">
      <div className="animate-fade-up flex flex-col gap-4">
        <Link href="/projects/gptlight" className="text-sm font-semibold hover:underline" style={{ color: "var(--accent)" }}>
          ← GPT-Light
        </Link>
        <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl">
          Classifieur de <span className="text-gradient">spam</span>
        </h1>
        <p className="text-lg text-muted">
          GPT-2 (124M) implémenté from scratch, chargé avec les poids officiels OpenAI, fine-tuné
          sur SMS Spam Collection — 96.96% accuracy sur le test set.
        </p>
      </div>

      <div className="card-glass glow-border flex flex-col gap-4 rounded-3xl p-7">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Collez un SMS ou un email court..."
          rows={4}
          maxLength={2000}
          className="rounded-xl border border-border bg-background-2 p-4 text-sm outline-none focus:border-border-strong"
        />
        <button
          onClick={() => handleSubmit(text)}
          disabled={loading || !text.trim()}
          className="w-fit rounded-full px-6 py-2.5 text-sm font-bold text-background transition-transform hover:scale-105 disabled:opacity-50 disabled:hover:scale-100"
          style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
        >
          {loading ? "Analyse en cours..." : "Classifier"}
        </button>

        <div className="flex flex-wrap gap-2 pt-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => {
                setText(ex);
                handleSubmit(ex);
              }}
              disabled={loading}
              className="rounded-full border border-border px-3 py-1 text-xs text-muted transition-colors hover:border-border-strong hover:text-foreground disabled:opacity-50"
            >
              {ex.slice(0, 40)}…
            </button>
          ))}
        </div>

        {error && <div className="rounded-xl border border-border bg-background-2 p-4 text-sm text-muted">{error}</div>}

        {result && (
          <div className="flex flex-col gap-3 border-t border-border pt-5">
            <div className="flex items-center gap-3">
              <span
                className="rounded-full px-4 py-1.5 text-sm font-extrabold uppercase tracking-wide"
                style={{
                  color: prediction === "spam" ? "var(--badge-infra-fg)" : "var(--badge-model-fg)",
                  background: prediction === "spam" ? "var(--badge-infra-bg)" : "var(--badge-model-bg)",
                }}
              >
                {prediction}
              </span>
              <span className="text-sm text-muted">
                {((prediction === "spam" ? result.spam : result.ham) * 100).toFixed(1)}% de confiance
              </span>
            </div>
            <div className="flex flex-col gap-2">
              {(["spam", "ham"] as const).map((label) => (
                <div key={label} className="flex items-center gap-3 text-xs">
                  <span className="w-10 uppercase text-muted">{label}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-background-2">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${result[label] * 100}%`,
                        background: "linear-gradient(90deg, var(--accent), var(--accent-2))",
                      }}
                    />
                  </div>
                  <span className="w-12 text-right text-muted">{(result[label] * 100).toFixed(1)}%</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
