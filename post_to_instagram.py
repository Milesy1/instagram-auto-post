#!/usr/bin/env python3
"""
Upload an image to Cloudinary and publish it to Instagram via the Graph API.

Usage:
    python post_to_instagram.py [stability|met]

    If a source is omitted, one is chosen at random each run.

    stability -- an abstract/geometric/glitch prompt is generated via the
                 Anthropic API and rendered via Stability AI.
    met       -- a real public-domain abstract/geometric artwork is pulled
                 from The Metropolitan Museum of Art's Open Access API,
                 with artist/title attribution added to the caption.

    Either way, the caption includes a random Shakespeare quote (Hamlet or
    The Tempest), the image is uploaded to Cloudinary, and published to
    Instagram.

Required environment variables (set these before running, don't hardcode secrets):
    CLOUDINARY_CLOUD_NAME     e.g. "prwmjb95"
    CLOUDINARY_UPLOAD_PRESET  e.g. "Miles1" (must be an UNSIGNED preset)
    IG_USER_ID                Instagram Business Account ID, e.g. "27321930254083396"
    IG_ACCESS_TOKEN           Long-lived Instagram access token
    ANTHROPIC_API_KEY         Only required for the "stability" source
    STABILITY_API_KEY         Only required for the "stability" source

    (The "met" source needs no API key at all.)

Example (PowerShell):
    $env:CLOUDINARY_CLOUD_NAME="prwmjb95"
    $env:CLOUDINARY_UPLOAD_PRESET="Miles1"
    $env:IG_USER_ID="27321930254083396"
    $env:IG_ACCESS_TOKEN="IGAAO6P7..."
    $env:ANTHROPIC_API_KEY="sk-ant-..."
    $env:STABILITY_API_KEY="sk-..."
    python post_to_instagram.py met
"""

import os
import sys
import time
import random
import tempfile
import requests

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.instagram.com/{GRAPH_API_VERSION}"

MET_API_BASE = "https://collectionapi.metmuseum.org/public/collection/v1"
MET_SEARCH_TERMS = ["abstract", "geometric abstraction", "cubism", "constructivism", "de stijl"]

SHAKESPEARE_QUOTES = [
    "We are such stuff as dreams are made on, and our little life is rounded with a sleep. -- The Tempest",
    "Hell is empty, and all the devils are here. -- The Tempest",
    "O brave new world, that has such people in't! -- The Tempest",
    "Though the seas threaten, they are merciful. -- The Tempest",
    "What's past is prologue. -- The Tempest",
    "To be, or not to be, that is the question. -- Hamlet",
    "There is nothing either good or bad, but thinking makes it so. -- Hamlet",
    "This above all: to thine own self be true. -- Hamlet",
    "The rest is silence. -- Hamlet",
    "There are more things in heaven and earth, Horatio, than are dreamt of in your philosophy. -- Hamlet",
]


def call_claude(api_key: str, prompt: str, max_tokens: int = 150) -> str:
    """Send a single-turn prompt to Claude and return the text response."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    return "".join(block["text"] for block in result["content"] if block["type"] == "text").strip()


def generate_caption() -> str:
    """Pick a random Shakespeare quote from Hamlet or The Tempest."""
    return random.choice(SHAKESPEARE_QUOTES)


def generate_image_prompt(api_key: str) -> str:
    """Generate a Stability AI prompt in an abstract/geometric/glitch style."""
    return call_claude(
        api_key,
        "Write one Stable Diffusion image generation prompt (max 40 words) for an "
        "abstract geometric composition with a minimal, glitch-art aesthetic -- think "
        "sharp angular forms, restrained monochrome or duotone palettes, subtle digital "
        "artifacts, clean negative space. No text or logos in the image. Return only the "
        "prompt itself, nothing else.",
        max_tokens=100,
    )


def generate_image_via_stability(prompt: str, api_key: str, output_path: str) -> None:
    """Generate an image from a prompt via Stability AI and save it as JPEG."""
    resp = requests.post(
        "https://api.stability.ai/v2beta/stable-image/generate/sd3",
        headers={
            "authorization": f"Bearer {api_key}",
            "accept": "image/*",
        },
        files={"none": ""},
        data={
            "prompt": prompt,
            "output_format": "jpeg",
            "aspect_ratio": "4:5",
        },
        timeout=60,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Stability AI generation failed: {resp.status_code} {resp.text}")

    with open(output_path, "wb") as f:
        f.write(resp.content)


def fetch_met_artwork(output_path: str, max_attempts: int = 15) -> str:
    """
    Search the Met Museum's Open Access API for a public-domain abstract/geometric
    artwork, download its image, and return an attribution string.
    """
    query = random.choice(MET_SEARCH_TERMS)
    search_resp = requests.get(
        f"{MET_API_BASE}/search",
        params={"q": query, "hasImages": "true"},
        timeout=30,
    )
    search_resp.raise_for_status()
    object_ids = search_resp.json().get("objectIDs") or []

    if not object_ids:
        raise RuntimeError(f"No Met results found for query '{query}'")

    random.shuffle(object_ids)

    for object_id in object_ids[:max_attempts]:
        obj_resp = requests.get(f"{MET_API_BASE}/objects/{object_id}", timeout=30)
        if obj_resp.status_code != 200:
            continue

        obj = obj_resp.json()
        image_url = obj.get("primaryImage")

        if not obj.get("isPublicDomain") or not image_url:
            continue

        image_resp = requests.get(image_url, timeout=60)
        if image_resp.status_code != 200:
            continue

        with open(output_path, "wb") as f:
            f.write(image_resp.content)

        title = obj.get("title") or "Untitled"
        artist = obj.get("artistDisplayName") or "Unknown artist"
        date = obj.get("objectDate") or ""
        attribution = f"{title} -- {artist}" + (f", {date}" if date else "")
        attribution += "\nThe Metropolitan Museum of Art, Open Access (CC0)"
        return attribution

    raise RuntimeError(f"Could not find a usable public-domain image for query '{query}' after {max_attempts} attempts")


def get_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def upload_to_cloudinary(image_path: str, cloud_name: str, upload_preset: str) -> str:
    """Upload image to Cloudinary (unsigned) and return a public JPEG URL."""
    url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"

    with open(image_path, "rb") as f:
        files = {"file": f}
        data = {"upload_preset": upload_preset}
        resp = requests.post(url, files=files, data=data, timeout=60)

    resp.raise_for_status()
    result = resp.json()

    if "error" in result:
        raise RuntimeError(f"Cloudinary upload failed: {result['error']}")

    secure_url = result["secure_url"]

    # Force JPEG delivery regardless of source format (Instagram requires JPEG).
    # Insert the f_jpg transformation right after "/upload/".
    jpeg_url = secure_url.replace("/upload/", "/upload/f_jpg/", 1)

    return jpeg_url


def create_media_container(ig_user_id: str, access_token: str, image_url: str, caption: str) -> str:
    """Create an Instagram media container and return its creation_id."""
    url = f"{GRAPH_BASE}/{ig_user_id}/media"
    data = {
        "image_url": image_url,
        "caption": caption,
        "access_token": access_token,
    }
    resp = requests.post(url, data=data, timeout=60)
    resp.raise_for_status()
    result = resp.json()

    if "error" in result:
        raise RuntimeError(f"Container creation failed: {result['error']}")

    return result["id"]


def wait_for_container_ready(creation_id: str, access_token: str, timeout_seconds: int = 60) -> None:
    """Poll the container status until it's FINISHED, or raise on error/timeout."""
    url = f"{GRAPH_BASE}/{creation_id}"
    params = {"fields": "status_code", "access_token": access_token}

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        result = resp.json()

        status = result.get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Container failed to process: {result}")

        time.sleep(2)

    raise TimeoutError("Container did not become ready in time.")


def publish_media(ig_user_id: str, access_token: str, creation_id: str) -> str:
    """Publish the container to the feed and return the published media ID."""
    url = f"{GRAPH_BASE}/{ig_user_id}/media_publish"
    data = {
        "creation_id": creation_id,
        "access_token": access_token,
    }
    resp = requests.post(url, data=data, timeout=60)
    resp.raise_for_status()
    result = resp.json()

    if "error" in result:
        raise RuntimeError(f"Publish failed: {result['error']}")

    return result["id"]


def main():
    if len(sys.argv) not in (1, 2) or (len(sys.argv) == 2 and sys.argv[1] not in ("stability", "met")):
        print("Usage: python post_to_instagram.py [stability|met]", file=sys.stderr)
        print("  If omitted, a source is chosen at random.", file=sys.stderr)
        sys.exit(1)

    source = sys.argv[1] if len(sys.argv) == 2 else random.choice(["stability", "met"])

    cloud_name = get_env("CLOUDINARY_CLOUD_NAME")
    upload_preset = get_env("CLOUDINARY_UPLOAD_PRESET")
    ig_user_id = get_env("IG_USER_ID")
    access_token = get_env("IG_ACCESS_TOKEN")

    print(f"Source: {source}")

    with tempfile.TemporaryDirectory() as tmpdir:
        image_path = os.path.join(tmpdir, "image.jpg")
        attribution = None

        if source == "stability":
            anthropic_key = get_env("ANTHROPIC_API_KEY")
            stability_key = get_env("STABILITY_API_KEY")

            print("Generating image prompt...")
            image_prompt = generate_image_prompt(anthropic_key)
            print(f"  -> {image_prompt}")

            print("Generating image via Stability AI...")
            generate_image_via_stability(image_prompt, stability_key, image_path)
            print(f"  -> saved to {image_path}")

        elif source == "met":
            print("Fetching a public-domain artwork from the Met...")
            attribution = fetch_met_artwork(image_path)
            print(f"  -> {attribution}")

        print("Selecting caption...")
        caption = generate_caption()
        print(f"  -> {caption}")

        if attribution:
            caption = f"{caption}\n\n{attribution}"

        caption = f"{caption}\n\nUploaded via Miles' Graph API"

        print("Uploading image to Cloudinary...")
        image_url = upload_to_cloudinary(image_path, cloud_name, upload_preset)
        print(f"  -> {image_url}")

        print("Creating Instagram media container...")
        creation_id = create_media_container(ig_user_id, access_token, image_url, caption)
        print(f"  -> creation_id: {creation_id}")

        print("Waiting for container to finish processing...")
        wait_for_container_ready(creation_id, access_token)
        print("  -> FINISHED")

        print("Publishing to Instagram...")
        media_id = publish_media(ig_user_id, access_token, creation_id)
        print(f"  -> Published! Media ID: {media_id}")


if __name__ == "__main__":
    main()
