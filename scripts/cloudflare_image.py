import os
import base64
import requests


CLOUDFLARE_IMAGE_MODEL = "@cf/lykon/dreamshaper-8-lcm"


def generate_image_cloudflare(
    prompt: str,
    output_path: str,
    seed: int = 1,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
    model: str = CLOUDFLARE_IMAGE_MODEL,
):
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()

    if not account_id:
        raise ValueError("Missing CLOUDFLARE_ACCOUNT_ID")
    if not api_token:
        raise ValueError("Missing CLOUDFLARE_API_TOKEN")

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "prompt": prompt,
        "seed": seed,
        "width": width,
        "height": height,
        "steps": steps,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=180)
    response.raise_for_status()

    data = response.json()
    if not data.get("success"):
        raise RuntimeError(f"Cloudflare API error: {data}")

    result = data.get("result", {})
    image_b64 = result.get("image")
    if not image_b64:
        raise RuntimeError(f"No image returned from Cloudflare: {data}")

    image_bytes = base64.b64decode(image_b64)

    folder = os.path.dirname(output_path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(output_path, "wb") as f:
        f.write(image_bytes)

    return output_path
