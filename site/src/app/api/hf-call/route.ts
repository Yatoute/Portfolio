import { NextRequest, NextResponse } from "next/server";

// Proxies the GPU-gated /gradio_api/call/<fn> step of a Hugging Face Space
// server-side so the request carries HF_TOKEN (never exposed to the browser).
// Anonymous calls to a ZeroGPU Space share a tiny, easily-exhausted quota
// ("ZeroGPU quota exceeded") — authenticating with the owner's token uses the
// account's real ZeroGPU allowance instead. File uploads (/gradio_api/upload)
// are NOT quota-gated and stay direct from the browser to the Space, both
// because they don't need auth and to avoid Vercel's serverless body-size
// limit on large video/audio uploads.
export const maxDuration = 60;

const SPACE_URLS: Record<string, string> = {
  "detection-images": "https://yatoute-detection-images-ia.hf.space",
  "detection-videos": "https://yatoute-detection-videos-ia.hf.space",
  "asr-togolais": "https://yatoute-asr-langues-togolaises.hf.space",
  "traduction-togolais": "https://yatoute-traduction-langues-togolaises.hf.space",
};

const ALLOWED_FNS: Record<string, string> = {
  "detection-images": "predict",
  "detection-videos": "predict",
  "asr-togolais": "transcribe",
  "traduction-togolais": "translate",
};

export async function POST(req: NextRequest) {
  const { space, data } = await req.json();
  const baseUrl = SPACE_URLS[space];
  const fn = ALLOWED_FNS[space];
  if (!baseUrl || !fn || !Array.isArray(data)) {
    return NextResponse.json({ error: "Requête invalide." }, { status: 400 });
  }

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (process.env.HF_TOKEN) headers.Authorization = `Bearer ${process.env.HF_TOKEN}`;

  const callRes = await fetch(`${baseUrl}/gradio_api/call/${fn}`, {
    method: "POST",
    headers,
    body: JSON.stringify({ data }),
  });
  if (!callRes.ok) {
    return NextResponse.json({ error: "Appel API échoué." }, { status: 502 });
  }
  const { event_id } = await callRes.json();

  const streamRes = await fetch(`${baseUrl}/gradio_api/call/${fn}/${event_id}`, {
    headers: process.env.HF_TOKEN ? { Authorization: `Bearer ${process.env.HF_TOKEN}` } : undefined,
  });
  const raw = await streamRes.text();
  const dataLine = raw.split("\n").find((l) => l.startsWith("data:"));
  if (!dataLine) {
    return NextResponse.json({ error: "Réponse inattendue du modèle." }, { status: 502 });
  }

  return NextResponse.json({ data: JSON.parse(dataLine.slice("data:".length).trim()) });
}
