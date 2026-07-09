from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen

DEFAULT_MODEL = "llama3.1:8b-instruct-q4_K_M"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_NUM_GPU = 999

SYSTEM_PROMPT = (
    "You rewrite selected text into clearer, more complete wording. "
    "Strictly follow the requested output length and format. "
    "Preserve the original meaning, tone, and language. "
    "Return only the rewritten text. Do not add explanations, labels, or markdown."
)

LENGTH_INSTRUCTIONS = {
    "two_sentences": "Output exactly 2 complete sentences. Each sentence must end with sentence punctuation.",
    "five_sentences": "Output exactly 5 complete sentences. Each sentence must end with sentence punctuation.",
    "paragraph": "Output exactly 10 complete sentences as one cohesive paragraph. Each sentence must end with sentence punctuation.",
}

TARGET_SENTENCE_COUNTS = {
    "two_sentences": 2,
    "five_sentences": 5,
    "paragraph": 10,
}


def is_local_ollama_endpoint(ollama_url: str) -> bool:
    parsed = urlparse(ollama_url)
    host = (parsed.hostname or "").strip().lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def load_config(config_path: Path | None) -> dict[str, Any]:
    if config_path is None or not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def get_setting(config: dict[str, Any], name: str, default: str) -> str:
    env_value = os.environ.get(name)
    if env_value:
        return env_value.strip()
    value = config.get(name.lower()) or config.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def get_int_setting(config: dict[str, Any], name: str, default: int) -> int:
    env_value = os.environ.get(name)
    if env_value and env_value.strip():
        try:
            return int(env_value.strip())
        except ValueError:
            return default

    value = config.get(name.lower())
    if value is None:
        value = config.get(name)

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def count_sentences(text: str) -> int:
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        return 0

    sentences = re.findall(r"[^.!?。！？]+[.!?。！？]+(?:[\"')\]]+)?", normalized)
    if sentences:
        return len(sentences)
    return 1


def build_user_prompt(text: str, length_mode: str) -> str:
    return f"{LENGTH_INSTRUCTIONS[length_mode]}\n\nSelected text:\n{text}"


def build_correction_prompt(original_text: str, previous_output: str, target_count: int) -> str:
    actual_count = count_sentences(previous_output)
    return (
        f"The previous rewrite had {actual_count} sentence(s), but it must have exactly "
        f"{target_count} sentence(s). Rewrite it again. Return exactly {target_count} complete "
        "sentences, with no bullets, numbering, labels, explanations, or markdown. "
        "Preserve the meaning, tone, and language of the selected text.\n\n"
        f"Selected text:\n{original_text}\n\nPrevious rewrite:\n{previous_output}"
    )


def send_ollama_chat(
    messages: list[dict[str, str]],
    model: str,
    ollama_url: str,
    timeout_seconds: int,
    num_gpu: int,
) -> str:
    payload = {
        "model": model,
        "stream": False,
        "messages": messages,
        "options": {
            "temperature": 0.2,
            "num_gpu": num_gpu,
        },
    }

    request = Request(
        f"{ollama_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        if is_local_ollama_endpoint(ollama_url):
            # Avoid corporate proxy interception for local Ollama calls.
            opener = build_opener(ProxyHandler({}))
            with opener.open(request, timeout=timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        else:
            with urlopen(request, timeout=timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 403 and is_local_ollama_endpoint(ollama_url):
            raise RuntimeError(
                "Ollama HTTP 403 while calling local endpoint. "
                "This is usually caused by a proxy intercepting localhost traffic. "
                "Ensure NO_PROXY includes localhost and 127.0.0.1. "
                f"Server response: {body}"
            ) from exc
        raise RuntimeError(f"Ollama HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not connect to Ollama at {ollama_url}: {exc.reason}") from exc

    message = data.get("message")
    if not isinstance(message, dict):
        raise RuntimeError(f"Unexpected Ollama response: {data}")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Ollama returned empty content: {data}")

    return content.strip()


def expand_with_ollama(
    text: str,
    model: str,
    ollama_url: str,
    timeout_seconds: int,
    length_mode: str,
    num_gpu: int,
) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(text, length_mode)},
    ]

    result = send_ollama_chat(messages, model, ollama_url, timeout_seconds, num_gpu)
    target_count = TARGET_SENTENCE_COUNTS.get(length_mode)
    if target_count is None:
        return result

    for _ in range(3):
        if count_sentences(result) == target_count:
            return result

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_correction_prompt(text, result, target_count)},
        ]
        result = send_ollama_chat(messages, model, ollama_url, timeout_seconds, num_gpu)

    actual_count = count_sentences(result)
    if actual_count != target_count:
        raise RuntimeError(
            f"Model returned {actual_count} sentence(s); expected exactly {target_count}."
        )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expand selected text with a local Ollama LLM.")
    parser.add_argument("--input", required=True, help="UTF-8 text file containing selected text.")
    parser.add_argument("--output", required=True, help="UTF-8 text file where expanded text will be written.")
    parser.add_argument("--config", help="Optional JSON config file path.")
    parser.add_argument(
        "--length",
        choices=sorted(LENGTH_INSTRUCTIONS),
        default="paragraph",
        help="Target expansion length.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    config_path = Path(args.config) if args.config else None

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    selected_text = input_path.read_text(encoding="utf-8").strip()
    if not selected_text:
        print("Selected text is empty.", file=sys.stderr)
        return 1

    try:
        config = load_config(config_path)
        model = get_setting(config, "LOCAL_LLM_MODEL", DEFAULT_MODEL)
        ollama_url = get_setting(config, "OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)
        timeout_seconds = int(config.get("timeout_seconds", 90))
        num_gpu = get_int_setting(config, "OLLAMA_NUM_GPU", DEFAULT_NUM_GPU)
        expanded_text = expand_with_ollama(
            selected_text,
            model,
            ollama_url,
            timeout_seconds,
            args.length,
            num_gpu,
        )
        output_path.write_text(expanded_text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"Local LLM expansion failed: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
