"""Independent content topics and immutable Telegram delivery destinations."""

from __future__ import annotations

import re
from urllib.parse import urlsplit


TOPICS = {
    "open_source_ai": "Open-Source AI",
    "ai_policy_law": "AI Policy & Law",
    "ai_models_products": "AI Models & Products",
}


def content_topics(title: str, summary: str = "") -> tuple[str, ...]:
    """Match every applicable topic, using content rather than publisher identity.

    Open weights/source-available releases share the open ecosystem destination;
    this routing label does not assert that their licenses are OSI-approved.
    """
    # Discovery excerpts often contain only article/comment URLs. URL slugs
    # are metadata, not a claim about the subject of the fetched story.
    text = re.sub(r"https?://\S+", " ", f"{title} {summary}").casefold()
    ai = bool(re.search(
        r"\b(?:ai|agi|llms?|artificial intelligence|machine learning|"
        r"language models?|models?|agents?|neural|inference|transformers?|pytorch|vllm|llama\.cpp|chatgpt|openai|"
        r"gpt[ -]?\d[\w.-]*|claude|gemini|qwen[\w.-]*|deepseek[\w.-]*|"
        r"llama[\w.-]*|mistral|glm[ -]?\d[\w.-]*)\b", text
    ))
    opened = ai and bool(re.search(
        r"\b(?:open[ -]source|open[ -]weights?|source[ -]available|"
        r"pytorch|vllm|llama\.cpp|transformers library)\b", text
    ))
    policy = ai and bool(re.search(
        r"\b(?:polic(?:y|ies)|regulat\w*|legislat\w*|laws?|legal|lawsuits?|"
        r"courts?|copyright|compliance|export controls?|enforcement|"
        r"ai act|executive orders?|antitrust|licens(?:e|es|ing)|licencing)\b", text
    ))
    model = bool(re.search(
        r"\b(?:models?|llms?|language models?|assistants?|chatbots?|"
        r"copilot|chatgpt|gpt[ -]?\d[\w.-]*|claude|gemini|qwen[\w.-]*|"
        r"deepseek[\w.-]*|llama[\w.-]*|mistral|glm[ -]?\d[\w.-]*|"
        r"ai (?:products?|apps?|applications?))\b", text
    ))
    improvement = bool(re.search(
        r"\b(?:releas\w*|launch\w*|introduc\w*|unveil\w*|improv\w*|"
        r"upgrad\w*|updat\w*|new|adds?|added|faster|better|"
        r"outperform\w*|benchmark\w*|capabilit\w*|performance|"
        r"context window|fine[ -]tun\w*|reasoning|built|builds?|"
        r"develop(?:s|ed)|train(?:s|ed)|capable)\b", text
    ))
    # Legal updates about models must not become model-improvement news merely
    # because they contain "new", "updated", or "models". Require a concrete
    # launch/performance signal when legal terminology is also present.
    if policy:
        improvement = bool(re.search(
            r"\b(?:models?|llms?|gpt[ -]?\d[\w.-]*|claude|gemini|qwen[\w.-]*|"
            r"deepseek[\w.-]*|llama[\w.-]*|mistral|glm[ -]?\d[\w.-]*)\b"
            r"[^.!?;]{0,50}\b(?:releas\w*|launch\w*|improv\w*|outperform\w*|"
            r"faster|better|upgrad\w*|benchmark\w*|reasoning)\b|"
            r"\b(?:releas\w*|launch\w*|unveil\w*)\s+(?:(?:a|an|the|new|open[ -]source|ai)\s+){0,4}"
            r"(?:models?|llms?|gpt[ -]?\d[\w.-]*|claude|gemini|qwen[\w.-]*|deepseek[\w.-]*|llama[\w.-]*|mistral)\b",
            text,
        ))
    # A question/opinion about whether models could be better is not itself
    # an announced model improvement.
    opinion_question = bool(re.match(r"\s*(?:why|should|could|would|will)\b", title.casefold())) and "?" in title
    flags = (opened, policy, ai and model and improvement and not opinion_question)
    return tuple(key for key, matched in zip(TOPICS, flags) if matched)


def parse_topic_link(link: str) -> tuple[int, int]:
    """Accept private forum topic/message links, including a copied message URL."""
    parsed = urlsplit(link.strip())
    if parsed.scheme != "https" or parsed.netloc != "t.me" or parsed.fragment:
        raise ValueError("Use a private Telegram topic link: https://t.me/c/GROUP/TOPIC")
    match = re.fullmatch(r"/c/([1-9]\d*)/([1-9]\d*)(?:/[1-9]\d*)?/?", parsed.path)
    if not match:
        raise ValueError("Use a private Telegram topic link: https://t.me/c/GROUP/TOPIC")
    # Telegram also copies message links as /c/GROUP/MESSAGE?thread=TOPIC.
    from urllib.parse import parse_qs
    query = parse_qs(parsed.query)
    if set(query) - {"thread", "single"}:
        raise ValueError("Unsupported Telegram topic link parameters")
    thread = query.get("thread", [match[2]])
    if len(thread) != 1 or not re.fullmatch(r"[1-9]\d*", thread[0]):
        raise ValueError("Invalid Telegram topic ID")
    return int("-100" + match[1]), int(thread[0])


def enqueue_topic_deliveries(connection, event_id: int, topics: tuple[str, ...], now: str) -> None:
    """Snapshot destinations in the same transaction as the relevance event."""
    mode = connection.execute("SELECT value FROM app_state WHERE key='telegram_routing_mode'").fetchone()
    if not mode or mode["value"] != "topics":
        return
    for topic in topics:
        connection.execute(
            """INSERT INTO relevance_telegram_topic_delivery(
                event_id,topic_key,chat_id,message_thread_id,updated_at)
                SELECT ?,topic_key,chat_id,message_thread_id,? FROM telegram_topic_route
                WHERE topic_key=? ON CONFLICT(event_id,chat_id,message_thread_id) DO NOTHING""",
            (event_id, now, topic),
        )
