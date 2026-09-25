import os
from dotenv import load_dotenv
import random
import json
import time

from typing import Dict, List, Type, TypeVar
from pydantic import BaseModel
from openai import OpenAI


from queryrewriter.generation.prompts import build_conversation_prompt
from queryrewriter.generation.schemas import (
    ArxivMetadata, 
    ConversationGenerationResponse,
    DEFAULT_TEMPERATURE_CONV, safe_json_parse
)
load_dotenv()


T = TypeVar("T", bound=BaseModel)

client = OpenAI()

CONVERSATION_STYLES = [
    "concise",
    "exploratory",
    "confused_but_curious",
    "structured_technical",
    "student_learning",
]

USER_PROFILES = [
    "beginner",
    "intermediate",
    "advanced",
]

def call_llm(
    system_prompt: str,
    user_prompt: str,
    response_model: Type[T],
    response_schema_name: str,
    temperature: float = 0.7,
    retries: int = 3,
) -> T:
    schema = response_model.model_json_schema()

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model="gpt-5-nano",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": response_schema_name,
                        "strict": True,
                        "schema": schema,
                    },
                },
            )

            content = response.choices[0].message.content
            data = json.loads(content)
            return response_model.model_validate(data)

        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def generate_llm_conversation(paper: ArxivMetadata) -> List[Dict[str, str]]:
    style = random.choice(CONVERSATION_STYLES)
    user_profile = random.choice(USER_PROFILES)

    system_prompt, user_prompt = build_conversation_prompt(paper, style, user_profile)
    response:ConversationGenerationResponse = call_llm(
        system_prompt, 
        user_prompt, 
        response_model=ConversationGenerationResponse,
        response_schema_name="conversation_generation_response",
        temperature=DEFAULT_TEMPERATURE_CONV
    )
    
    return [msg.model_dump() for msg in response.conversation]