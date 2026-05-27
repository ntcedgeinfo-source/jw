import os
import base64
import requests


CLOUDFLARE_IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"

# You may change this depending on what is available in your Cloudflare account.
# This model is commonly used for text generation examples.
CLOUDFLARE_TEXT_MODEL = "@cf/meta/llama-3-8b-instruct"


def get_cloudflare_credentials():
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()

    if not account_id:
        raise ValueError("Missing CLOUDFLARE_ACCOUNT_ID")
    if not api_token:
        raise ValueError("Missing CLOUDFLARE_API_TOKEN")

    return account_id, api_token


def run_cloudflare_ai(model: str, payload: dict, timeout: int = 180):
    account_id, api_token = get_cloudflare_credentials()

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=timeout,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("success"):
        raise RuntimeError(f"Cloudflare API error: {data}")

    return data.get("result", {})


def generate_image_cloudflare(
    prompt: str,
    output_path: str,
    seed: int = 1,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
    model: str = CLOUDFLARE_IMAGE_MODEL,
):
    payload = {
        "prompt": prompt,
        "seed": seed,
        "width": width,
        "height": height,
        "steps": steps,
    }

    result = run_cloudflare_ai(model=model, payload=payload)

    image_b64 = result.get("image")

    if not image_b64:
        raise RuntimeError(f"No image returned from Cloudflare: {result}")

    image_bytes = base64.b64decode(image_b64)

    folder = os.path.dirname(output_path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(output_path, "wb") as f:
        f.write(image_bytes)

    return output_path


def explain_image_prompt_cloudflare(
    prompt: str,
    model: str = CLOUDFLARE_TEXT_MODEL,
):
    system_prompt = """
You are an AI image explainer.
Explain the image prompt in simple English.

Your output must include:
1. Main subject
2. Visual style
3. Important details
4. Possible use case
5. Improved prompt suggestion

Keep the explanation clear, short, and useful.
"""

    user_prompt = f"""
Explain this image prompt:

{prompt}
"""

    payload = {
        "messages": [
            {
                "role": "system",
                "content": system_prompt.strip(),
            },
            {
                "role": "user",
                "content": user_prompt.strip(),
            },
        ]
    }

    result = run_cloudflare_ai(model=model, payload=payload)

    # Cloudflare text models may return different keys depending on the model.
    explanation = (
        result.get("response")
        or result.get("text")
        or result.get("answer")
        or result.get("output")
    )

    if not explanation:
        raise RuntimeError(f"No explanation returned from Cloudflare: {result}")

    return explanation


def generate_image_with_explainer(
    prompt: str,
    output_path: str,
    seed: int = 1,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
):
    image_path = generate_image_cloudflare(
        prompt=prompt,
        output_path=output_path,
        seed=seed,
        width=width,
        height=height,
        steps=steps,
    )

    explanation = explain_image_prompt_cloudflare(prompt)

    return {
        "image_path": image_path,
        "explanation": explanation,
    }


if __name__ == "__main__":
    prompt = """
A professional AI-powered government dashboard interface,
clean layout, blue and gold color theme, modern cards,
data charts, soft shadows, mobile and web responsive design,
high-quality UI mockup.
"""

    result = generate_image_with_explainer(
        prompt=prompt,
        output_path="outputs/generated_dashboard.png",
        seed=7,
        width=1024,
        height=1024,
        steps=4,
    )

    print("Image saved to:", result["image_path"])
    print("\nAI Explainer:\n")
    print(result["explanation"])
