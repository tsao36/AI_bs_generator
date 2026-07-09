import argparse
import json
import os
import sys
from pathlib import Path

import requests


def call_ai_expand(text: str) -> str:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("LOCAL_LLM_MODEL", "qwen2.5:7b-instruct")

    url = f"{base_url}/api/chat"
    headers = {"Content-Type": "application/json"}

    system_prompt = (
        "You rewrite selected text into a longer, clearer sentence or short paragraph "
        "while preserving the original meaning, tone, and language. "
        "Return only the rewritten text with no extra notes."
    )

    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    }

    resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
    resp.raise_for_status()
    data = resp.json()

    try:
        return data["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Unexpected API response format: {data}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Expand text with AI")
    parser.add_argument("--input", required=True, help="Input text file path")
    parser.add_argument("--output", required=True, help="Output text file path")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    text = input_path.read_text(encoding="utf-8")
    if not text.strip():
        print("Input text is empty.", file=sys.stderr)
        return 1

    try:
        expanded = call_ai_expand(text)
    except Exception as exc:  # noqa: BLE001
        print(f"AI request failed: {exc}", file=sys.stderr)
        return 2

    output_path.write_text(expanded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
