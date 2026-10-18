// Client-side helper for calling a Hugging Face Space's GPU-gated function
// through the /api/hf-call dispatch proxy (see that route for why dispatch
// and poll are split). Parses the SSE response properly: an HF-side error
// ("event: error") carries a "data:" line too, so a naive "find the first
// data: line" treats it as a successful result — the caller's .label /
// .confidences access then throws something unrelated, and a generic
// catch-all swallows the real cause. This surfaces it instead.
export async function callHfSpace(space: string, data: unknown[]): Promise<unknown[]> {
  const dispatchRes = await fetch("/api/hf-call", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ space, data }),
  });
  const dispatch = await dispatchRes.json();
  if (!dispatchRes.ok || dispatch.error) {
    throw new Error(dispatch.error || "Appel API échoué.");
  }

  const streamRes = await fetch(dispatch.pollUrl);
  const raw = await streamRes.text();
  const blocks = raw.split("\n\n").filter((b) => b.trim());

  for (const block of blocks.reverse()) {
    const lines = block.split("\n");
    const eventLine = lines.find((l) => l.startsWith("event:"));
    const dataLine = lines.find((l) => l.startsWith("data:"));
    if (!dataLine) continue;

    const parsed = JSON.parse(dataLine.slice("data:".length).trim());
    if (eventLine?.slice("event:".length).trim() === "error") {
      const message = parsed && typeof parsed === "object" && "error" in parsed ? parsed.error : "Erreur du modèle.";
      throw new Error(String(message));
    }
    return parsed;
  }

  throw new Error("Réponse inattendue du modèle.");
}
