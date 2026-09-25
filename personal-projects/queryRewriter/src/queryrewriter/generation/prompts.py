from typing import Tuple, List, Dict
import json
from dataclasses import asdict

from queryrewriter.generation.schemas import ArxivMetadata, RewriteExample


def build_conversation_prompt(paper: ArxivMetadata, style: str, user_profile: str) -> Tuple[str, str]:
  system_prompt = """
You generate realistic conversations for training a query rewriting model used in a RAG ChatDocument system.

The conversation is about ONE scientific paper only.

Use ONLY information grounded in the paper title and abstract.

────────────────
CONVERSATION STYLE
────────────────

Write the conversation as if a real user is chatting with a research assistant.

User questions must look like natural human questions.

Do NOT use any annotation style.

NEVER include prefixes such as:

- "Q:"
- "A:"
- "Query:"
- "Answer:"
- "User:"
- "Assistant:"

Never include meta commentary such as:

- "according to the abstract"
- "the paper states"
- "the abstract mentions"

The user should simply ask questions naturally.

────────────────
CONVERSATION STRUCTURE
────────────────

Generate 6-8 user turns.

Among these user turns:
- at most 1 should already be retrieval-ready and require no rewriting
- at least 5 should require rewriting

The turns that require rewriting must include:
- at least 1 coreference case (ex: "this method", "that result", "their approach")
- at least 1 ellipsis case (ex: "and how does that work?")
- at least 1 conversational-noise case (ex: "so basically", "I mean", "like")
- at least 1 vague follow-up question

Assistant answers should be short and plausible.

────────────────
IMPORTANT
────────────────

The conversation must feel like a real discussion with a researcher reading a paper.

Do NOT invent implementation details not suggested by the abstract.

Return JSON only.

Output schema:

{
  "conversation": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "notes": {
    "style": "...",
    "user_profile": "..."
  }
}
""".strip()

  user_prompt = f"""
Paper metadata:
- title: {paper.title}
- entry_id: {paper.entry_id}
- primary_category: {paper.primary_category}
- categories: {paper.categories}
- authors: {paper.authors[:6]}
- published: {paper.published}
- comment: {paper.comment}

Abstract:
{paper.abstract}

Style:
- conversation_style: {style}
- user_profile: {user_profile}

Generate one realistic conversation about this paper.
""".strip()

  return system_prompt, user_prompt


def build_extraction_prompt(paper: ArxivMetadata, conversation: List[Dict[str, str]]) -> Tuple[str, str]:

  system_prompt = """
You extract supervised training examples for a RAG query rewriting model.

The goal is to convert conversation turns into examples for a retrieval query rewriting system.

The rewritten query must help a retrieval system find passages that contain the information needed to answer the user's question.

────────────────
EXTRACTION RULE
────────────────

Generate ONE training example for EVERY user question in the conversation.

Do not skip questions.

If a conversation contains N user turns, the output must contain N examples.

Even if the question is already clear and does not require rewriting, you must still generate an example with:

should_rewrite = false  
rewritten_query = user_query

Each user question must correspond to exactly one example.

────────────────
REWRITE GOAL
────────────────

The goal of rewriting is NOT to answer the question.

The goal is to help the retrieval system find the **evidence needed to answer the question**.

Rewrite the query so that a retrieval system can retrieve passages describing the relevant concepts, properties, methods, or results.

────────────────
INFORMATION-SEEKING RULE
────────────────

Rewritten queries must be **information-seeking**, not confirmation-seeking.

Do NOT rewrite questions as:

- "Is there evidence that..."
- "Does the paper prove that..."
- "Is it true that..."

These formulations bias retrieval toward confirmations rather than retrieving the underlying information.

Instead, rewrite the query so that the retrieval system can find the **facts required to determine the answer**.

Example:

User query:
"Is Yatoute the only author of the book?"

Bad rewrite:
"Is Yatoute the only author of the book?"

Good rewrite:
"Who are the authors of the book?"

The retrieval system should retrieve the relevant facts, not a yes/no statement.

────────────────
REWRITE STYLE
────────────────

The rewritten query must be a clear, natural, semantically meaningful question.

The rewritten query must:

- preserve the user's information need (not necessarily the exact wording)
- include missing context from the conversation
- be understandable without conversation history
- retrieve the information required to answer the question

The rewrite should favor **information-seeking questions**, such as:

- "What..."
- "How..."
- "Which..."
- "Who..."
- "Why..."

These forms retrieve better evidence from documents.

Do NOT produce keyword-style search queries.

Do NOT produce document titles or summaries.

Do NOT introduce information that was not present in the conversation.

────────────────
CLEANING RULES
────────────────

Remove conversational artifacts such as:

- "so"
- "I mean"
- "basically"
- "like"
- filler words
- "this paper"
- "this method"

Remove annotation artifacts such as:

- "Q:"
- "Query:"
- "Question:"
- "the abstract says"

The rewritten query must sound like a normal user question.

────────────────
WHEN TO REWRITE
────────────────

Set should_rewrite = true if the user query:

• contains references to previous turns  
• contains conversational noise  
• is vague without context  
• requires clarification for retrieval

Set should_rewrite = false if the query is already clear and retrieval-ready.

If should_rewrite = false then:

rewritten_query MUST be identical to user_query.

────────────────
HISTORY RULE
────────────────

For each example, include only the minimal conversation history required to interpret the query.

History may be empty if the question is already standalone.

Do not include the entire conversation if it is not necessary.

────────────────
OUTPUT
────────────────

Return valid JSON only.

The output must contain one example per user question.

{
  "examples": [
    {
      "history": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}
      ],
      "user_query": "...",
      "rewritten_query": "...",
      "should_rewrite": true,
      "rewrite_type": ["coreference_resolution"],
      "difficulty": "easy"
    }
  ]
}
""".strip()


  user_prompt = f"""
Paper metadata:
- title: {paper.title}
- entry_id: {paper.entry_id}
- primary_category: {paper.primary_category}
- categories: {paper.categories}
- authors: {paper.authors[:6]}

Abstract:
{paper.abstract}

Conversation:
{json.dumps(conversation, ensure_ascii=False, indent=2)}
""".strip()

  return system_prompt, user_prompt


def build_validation_prompt(example: RewriteExample) -> Tuple[str, str]:
  system_prompt = """
You validate a query rewriting example for a ChatPDF retrieval system.

Check the following criteria.

1. The rewritten query preserves the user's intent.

2. All references to previous conversation context are correctly resolved.

3. Conversational noise has been removed.

4. The rewritten query is suitable for semantic retrieval.

5. If should_rewrite=false then rewritten_query MUST equal user_query.

6. The rewritten query follows ONE of these styles:

STYLE A — standalone question

Example:
"How does the model handle modality collapse?"

STYLE B — compact retrieval query

Example:
"modality collapse mitigation multimodal LLM"

The query MUST NOT mix the two styles.

Reject examples containing:

• "Q:"
• "Query:"
• "A:"
• annotation artifacts
• meta commentary

Return JSON only.

{
  "valid": true,
  "reason": "...",
  "corrected_rewritten_query": "...",
  "corrected_should_rewrite": true,
  "corrected_rewrite_type": ["coreference_resolution"],
  "corrected_difficulty": "easy"
}
""".strip()

  user_prompt = json.dumps(asdict(example), ensure_ascii=False, indent=2)
  return system_prompt, user_prompt