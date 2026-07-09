import argparse
import base64
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List

import requests
from dotenv import load_dotenv


load_dotenv()


def guess_mime(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "image/gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def build_chat_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def make_payload(model: str, image_bytes: bytes) -> dict:
    mime = guess_mime(image_bytes)
    img_b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an OCR transcriber. Return only the text visible in the image. "
                    "Do not summarize, translate, or infer. Preserve line breaks and bullet structure."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Transcribe all readable text exactly as shown. "
                            "Keep original reading order, line breaks, and list markers. "
                            "For unreadable words, use [?]. "
                            "If there is no readable text, return exactly: [NO_TEXT]"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{img_b64}",
                            "detail": "high",
                        },
                    },
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": 800,
    }


def parse_models(raw: str) -> List[str]:
    return [m.strip() for m in (raw or "").split(",") if m.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare OCR output across multiple VLM models.")
    parser.add_argument("--image", required=True, help="Path to image file to OCR.")
    parser.add_argument(
        "--models",
        default="gemma3:12b,llava:13b",
        help="Comma-separated model list. Example: gemma3:12b,llava:13b",
    )
    parser.add_argument("--timeout", type=int, default=120, help="Request timeout in seconds.")
    args = parser.parse_args()

    base_url = (os.getenv("VLM_BASE_URL") or "").strip()
    api_key = (os.getenv("VLM_API_KEY") or "").strip()
    if not base_url:
        print("[Error] VLM_BASE_URL is not set.")
        return 1

    img_path = Path(args.image)
    if not img_path.is_absolute():
        img_path = Path.cwd() / img_path
    if not img_path.exists():
        print(f"[Error] Image not found: {img_path}")
        return 1

    models = parse_models(args.models)
    if not models:
        print("[Error] No models provided.")
        return 1

    image_bytes = img_path.read_bytes()
    url = build_chat_url(base_url)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    sections: List[str] = []
    print(f"[Info] Comparing {len(models)} model(s) on: {img_path}")
    print(f"[Info] Endpoint: {url}")

    for model in models:
        print(f"[Run] model={model}")
        payload = make_payload(model, image_bytes)
        try:
            resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=args.timeout)
            status = resp.status_code
            if status != 200:
                text = resp.text[:2000]
                body = f"[HTTP {status}]\n{text}"
            else:
                data = resp.json()
                body = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                if not body:
                    body = "[EMPTY_RESPONSE]"
        except Exception as exc:
            status = -1
            body = f"[ERROR] {exc}"

        sections.append(
            "\n".join(
                [
                    f"## Model: {model}",
                    f"Status: {status}",
                    "",
                    body.strip(),
                ]
            )
        )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(f"ocr_compare_{stamp}.md")
    out_path.write_text(
        "\n\n".join(
            [
                f"# OCR Compare\n",
                f"Image: {img_path}",
                f"Endpoint: {url}",
                "",
                *sections,
            ]
        ),
        encoding="utf-8",
    )
    print(f"[OK] Saved compare report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
