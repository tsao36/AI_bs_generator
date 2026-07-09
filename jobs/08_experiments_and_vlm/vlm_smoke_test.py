import base64
import io
import json
import os
import sys
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv


load_dotenv()


def read_image_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except Exception as exc:  # noqa: BLE001
        print(f"[Error] Failed to read image file '{path}': {exc}")
        sys.exit(1)


def make_test_image_bytes() -> bytes:
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print("[Error] Pillow is required for this test. Install with: pip install Pillow")
        print(f"[Detail] {exc}")
        sys.exit(1)

    img = Image.new("RGB", (320, 160), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((20, 60), "LLAVA TEST 123", fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_payload(model: str, image_bytes: bytes) -> dict:
    img_b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Extract any visible text from the image. If no text, give a brief caption.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                    },
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 256,
    }


def main() -> int:
    base_url = (os.getenv("VLM_BASE_URL") or "").strip().rstrip("/")
    model = (os.getenv("VLM_MODEL") or "").strip()
    api_key = (os.getenv("VLM_API_KEY") or "").strip()

    if not base_url or not model:
        print("[Error] Set VLM_BASE_URL and VLM_MODEL in your environment before running this test.")
        return 1

    user_choice = input("Enter image filename in this folder (or leave empty to use built-in test image): ").strip()
    if user_choice:
        img_path = Path(user_choice)
        if not img_path.is_absolute():
            img_path = Path.cwd() / img_path
        if not img_path.exists():
            print(f"[Error] File not found: {img_path}")
            return 1
        image_bytes = read_image_bytes(img_path)
        print(f"[Info] Using user image: {img_path}")
    else:
        image_bytes = make_test_image_bytes()
        print("[Info] Using built-in test image (LLAVA TEST 123)")

    payload = build_payload(model, image_bytes)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = f"{base_url}/v1/chat/completions"
    print(f"[Info] POST {url} model={model}")

    resp = requests.post(url, headers=headers, data=json.dumps(payload))
    print(f"[Info] Status: {resp.status_code}")

    if resp.status_code != 200:
        print(resp.text[:2000])
        return 2

    data = resp.json()
    content: Optional[str] = None
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        pass

    print("\n[Response]")
    print(content or json.dumps(data, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
