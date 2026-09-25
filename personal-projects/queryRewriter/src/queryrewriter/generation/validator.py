from typing import Optional

from queryrewriter.generation.generator import call_llm
from queryrewriter.generation.prompts import build_validation_prompt
from queryrewriter.generation.schemas import RewriteExample, ValidationResponse, DEFAULT_TEMPERATURE_VALIDATE


def validate_example(ex: RewriteExample) -> Optional[RewriteExample]:
    system_prompt, user_prompt = build_validation_prompt(ex)

    response: ValidationResponse = call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=ValidationResponse,
        response_schema_name="validation_response",
        temperature=DEFAULT_TEMPERATURE_VALIDATE,
    )

    if not response.valid:
        return None

    return RewriteExample(
        entry_id=ex.entry_id,
        primary_category=ex.primary_category,
        categories=ex.categories,
        history=ex.history,
        user_query=ex.user_query,
        rewritten_query=response.corrected_rewritten_query,
        should_rewrite=response.corrected_should_rewrite,
        rewrite_type=response.corrected_rewrite_type,
        difficulty=response.corrected_difficulty,
        source=ex.source,
        metadata=ex.metadata,
    )