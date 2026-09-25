// Fly.io hosts the compute (torch/whisperx/openclip, FastAPI, Gradio) — these
// demo pages call them as pure JSON/SSE APIs from the browser. Navigation
// never leaves the Next.js site; only background fetch() calls cross origin.
export const GPTLIGHT_API = "https://gptlight-spam-yatoute-demo.fly.dev";
export const CHATPDF_API = "https://chatpdf-yatoute-demo.fly.dev";
export const VIDEOMINING_API = "https://video-mining-yatoute-demo.fly.dev";
