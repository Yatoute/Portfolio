Hello,

What stands out in this role is the emphasis on production-grade AI systems, not prototypes — that's the part of the job I can speak to most directly. At Opsci AI, I built an LLM agent that monitors dozens of scraping Cloud Functions in production, diagnoses the exact failure (schema drift, expired session, rate-limiting), fixes the code itself, and only deploys after a mandatory local validation loop — reproducing the bug, checking the fix, capturing the current revision, deploying on a dedicated branch. It runs with real guardrails: no scope creep beyond the minimal fix, mandatory human confirmation before any irreversible action, changes committed under its own service identity for full traceability. That's the same discipline your description of guardrails, observability, and reliable agentic workflows is asking for.

On the retrieval/agent side, I built a LangGraph agent (ChatPDF) with FAISS semantic search, chunk navigation, and page-level lookup as tools rather than plain top-k retrieval. I also fine-tune open-source models (LoRA/QLoRA on Qwen, GPT-2 from scratch), and I rebuilt a translation engine to run in-process with CTranslate2, cutting GCP infrastructure cost by 90% and increasing throughput 77x once it hit real production traffic — the kind of cost/latency tradeoff your "iterate for impact" point is about.

I should be direct about the gap: the 5+ years bar is more than my actual track record, which is about a year and a half across two internships, with my degree finishing in 2026. I'd rather flag that upfront than have it surface later — what I can offer instead is systems that have already run in production and been measured, not just built.

My GitHub (github.com/Yatoute) and portfolio (yatoute.vercel.app) have these projects in detail, with live, testable demos.

Happy to talk whenever works for you.

Best,
Yatoute Mintoama
