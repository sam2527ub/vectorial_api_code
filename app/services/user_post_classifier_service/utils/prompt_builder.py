"""Build system and user prompts for post classification."""
import os
from typing import Any, Dict, List, Optional


MAX_EXAMPLES_LENGTH = int(os.getenv("MAX_EXAMPLES_LENGTH", "80000"))
MAX_EXAMPLE_POST_LENGTH = int(os.getenv("MAX_EXAMPLE_POST_LENGTH", "2000"))


def _format_examples(classifier_examples: Optional[Any]) -> str:
    """Build few-shot examples string; respects MAX_EXAMPLES_LENGTH and MAX_EXAMPLE_POST_LENGTH."""
    if not classifier_examples:
        return ""

    examples_text = ""
    if isinstance(classifier_examples, list):
        for idx, example in enumerate(classifier_examples):
            if not isinstance(example, dict):
                continue
            example_post = example.get("post") or example.get("text", "")
            example_labels = example.get("labels", [])
            example_label = example.get("label", "")
            example_score = example.get("score", "")

            if len(example_post) > MAX_EXAMPLE_POST_LENGTH:
                example_post = example_post[:MAX_EXAMPLE_POST_LENGTH] + "... [truncated]"

            label_display = ", ".join(example_labels) if isinstance(example_labels, list) and example_labels else (example_label or "")
            if not example_post or not label_display:
                continue

            line = f"\n\nExample {idx + 1}:\nPost: {example_post}\nLabel(s): {label_display}"
            if example_score:
                line += f" (Score: {example_score})"
            if len(examples_text) + len(line) > MAX_EXAMPLES_LENGTH:
                break
            examples_text += line

    elif isinstance(classifier_examples, dict):
        for idx, (key, value) in enumerate(classifier_examples.items()):
            if not isinstance(value, dict):
                continue
            example_post = value.get("post") or value.get("text", "")
            example_labels = value.get("labels", [])
            example_label = value.get("label", key)

            if len(example_post) > MAX_EXAMPLE_POST_LENGTH:
                example_post = example_post[:MAX_EXAMPLE_POST_LENGTH] + "... [truncated]"

            label_display = ", ".join(example_labels) if isinstance(example_labels, list) and example_labels else (example_label or key)
            if not example_post or not label_display:
                continue

            line = f"\n\nExample {idx + 1}:\nPost: {example_post}\nLabel(s): {label_display}"
            if len(examples_text) + len(line) > MAX_EXAMPLES_LENGTH:
                break
            examples_text += line

    return examples_text


def build_classifier_prompts(
    posts_texts: List[str],
    classifier_name: str,
    classifier_prompt: str,
    classifier_description: str,
    classifier_labels: List[str],
    classifier_examples: Optional[Any],
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt)."""
    examples_text = _format_examples(classifier_examples)

    system_parts = []
    system_parts.append(
        classifier_prompt
        or f"You are a {classifier_name} classifier. Classify posts according to the available labels."
    )
    if classifier_labels:
        system_parts.append(f"\n\nAvailable Labels: {', '.join(classifier_labels)}")
    if classifier_description:
        system_parts.append(f"\n\nAdditional Context: {classifier_description}")
    if examples_text:
        system_parts.append(
            f"\n\nBelow are example posts with their correct classifications. Use them as ground-truth demonstrations for how to classify future posts:{examples_text}"
        )
    system_prompt = "\n".join(system_parts)

    num_posts = len(posts_texts)
    posts_section = ""
    for idx, text in enumerate(posts_texts, 1):
        posts_section += f"\n\n<<<POST_ID_{idx}_START>>>\n{text}\n<<<POST_ID_{idx}_END>>>"

    user_prompt = f"""
        Classify exactly {num_posts} posts. Each <<<POST_ID_X_START>>>...<<<POST_ID_X_END>>> block = 1 post.

        {posts_section}

        Return JSON with exactly {num_posts} classifications in this structure:

        {{
        "classifications": [
            {{"post_id": 1, "label": "winning_label", "score": 0.85}},
            {{"post_id": 2, "label": "winning_label", "score": 0.92}},
            ...
            {{"post_id": {num_posts}, "label": "winning_label", "score": 0.95}}
        ]
        }}

        Rules:
        1. Each post must have *exactly one label* (e.g. "useful" or one of the NOT USEFUL reason labels).
        2. "score" must be a number between 0.0 and 1.0 (confidence).
        3. Array length must equal {num_posts}, order match post_id 1 through {num_posts}.
        4. Respond *only with valid JSON*, no explanations, no markdown.
    """
    return system_prompt, user_prompt
