import random
from typing import List, Dict

from queryrewriter.generation.schemas import ArxivMetadata, RewriteExample


TEMPLATE_PATTERNS = [
    {
        "user_query_template": "How is this model implemented?",
        "rewrite_template": "How is {topic} implemented?",
        "rewrite_type": ["coreference_resolution"],
        "difficulty": "medium",
        "should_rewrite": True,
    },
    {
        "user_query_template": "What are its limitations?",
        "rewrite_template": "What are the limitations of {topic}?",
        "rewrite_type": ["coreference_resolution"],
        "difficulty": "easy",
        "should_rewrite": True,
    },
    {
        "user_query_template": "And the datasets?",
        "rewrite_template": "What datasets are used in {topic}?",
        "rewrite_type": ["ellipsis_resolution"],
        "difficulty": "medium",
        "should_rewrite": True,
    },
    {
        "user_query_template": "What is dropout?",
        "rewrite_template": "What is dropout?",
        "rewrite_type": ["no_change"],
        "difficulty": "easy",
        "should_rewrite": False,
    },
]


HISTORY_TEMPLATES = [
    [
        {"role": "user", "content": "Can you summarize this paper?"},
        {"role": "assistant", "content": "This paper proposes or studies {topic}."},
    ],
    [
        {"role": "user", "content": "Explain the main method in this paper."},
        {"role": "assistant", "content": "The paper focuses on {topic}."},
    ],
]


def render_template_history(history_template: List[Dict[str, str]], topic: str) -> List[Dict[str, str]]:
    return [
        {"role": msg["role"], "content": msg["content"].format(topic=topic)}
        for msg in history_template
    ]


def generate_template_examples(paper: ArxivMetadata, per_paper_limit: int = 6) -> List[RewriteExample]:
    topic = paper.title
    examples = []

    chosen_patterns = random.sample(TEMPLATE_PATTERNS, k=min(len(TEMPLATE_PATTERNS), per_paper_limit))
    for pattern in chosen_patterns:
        history_template = random.choice(HISTORY_TEMPLATES)
        history = render_template_history(history_template, topic)

        examples.append(
            RewriteExample(
                entry_id=paper.entry_id,
                primary_category=paper.primary_category,
                categories=paper.categories,
                history=history,
                user_query=pattern["user_query_template"],
                rewritten_query=pattern["rewrite_template"].format(topic=topic),
                should_rewrite=pattern["should_rewrite"],
                rewrite_type=pattern["rewrite_type"],
                difficulty=pattern["difficulty"],
                source="template",
                metadata={"title": paper.title},
            )
        )

    return examples