from typing import Dict, List

from queryrewriter.generation.generator import call_llm
from queryrewriter.generation.prompts import build_extraction_prompt
from queryrewriter.generation.schemas import (
    ArxivMetadata,
    RewriteExample,
    ExtractedExamplesResponse,
    DEFAULT_TEMPERATURE_EXTRACT,
)


def extract_examples_from_conversation(
    paper: ArxivMetadata,
    conversation: List[Dict[str, str]],
    conv_idx: int,
) -> List[RewriteExample]:

    system_prompt, user_prompt = build_extraction_prompt(paper, conversation)

    response: ExtractedExamplesResponse = call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=ExtractedExamplesResponse,
        response_schema_name="extracted_examples_response",
        temperature=DEFAULT_TEMPERATURE_EXTRACT,
    )

    examples: List[RewriteExample] = []

    for ex in response.examples:

        history = [msg.model_dump() for msg in ex.history]

        # Ignorer les first-turn (history vide)
        if len(history) and not ex.should_rewrite:
            continue

        example = RewriteExample(
            entry_id=paper.entry_id,
            primary_category=paper.primary_category,
            categories=paper.categories,
            history=history,
            user_query=ex.user_query,
            rewritten_query=ex.rewritten_query,
            should_rewrite=ex.should_rewrite,
            rewrite_type=ex.rewrite_type,
            difficulty=ex.difficulty,
            source="llm_conversation",
            metadata={"conv_idx": conv_idx, "title": paper.title},
        )

        examples.append(example)

    return examples