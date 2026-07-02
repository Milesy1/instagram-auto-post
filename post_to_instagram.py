#!/usr/bin/env python3
"""
Upload an image to Cloudinary and publish it to Instagram via the Graph API.

Usage:
    python post_to_instagram.py [stability|met|nasa|artic|commons]

    If a source is omitted, one is chosen at random each run.

Required environment variables (set these before running, don't hardcode secrets):
    CLOUDINARY_CLOUD_NAME     e.g. "prwmjb95"
    CLOUDINARY_UPLOAD_PRESET  e.g. "Miles1" (must be an UNSIGNED preset)
    IG_USER_ID                Instagram Business Account ID, e.g. "27321930254083396"
    IG_ACCESS_TOKEN           Long-lived Instagram access token
    ANTHROPIC_API_KEY         Only required for the "stability" source
    STABILITY_API_KEY         Only required for the "stability" source
    NASA_API_KEY              Optional, only for the "nasa" source (defaults to DEMO_KEY)
"""

import os
import re
import sys
import time
import random
import tempfile
import requests

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.instagram.com/{GRAPH_API_VERSION}"

MET_API_BASE = "https://collectionapi.metmuseum.org/public/collection/v1"
MET_SEARCH_TERMS = ["abstract", "geometric abstraction", "cubism", "constructivism", "de stijl"]

NASA_APOD_URL = "https://api.nasa.gov/planetary/apod"

ARTIC_API_BASE = "https://api.artic.edu/api/v1/artworks"
ARTIC_SEARCH_TERMS = ["abstract", "geometric abstraction", "cubism", "constructivism", "de stijl", "minimalism"]

WIKIMEDIA_API_BASE = "https://commons.wikimedia.org/w/api.php"
WIKIMEDIA_SEARCH_TERMS = ["fractal", "chaos theory", "geometric abstract", "strange attractor"]
WIKIMEDIA_HEADERS = {"User-Agent": "instagram-auto-post/1.0 (https://github.com/milesy1/instagram-auto-post)"}

SHAKESPEARE_QUOTES = [
    "We are such stuff as dreams are made on, and our little life is rounded with a sleep. -- The Tempest",
    "Hell is empty, and all the devils are here. -- The Tempest",
    "O brave new world, that has such people in't! -- The Tempest",
    "Though the seas threaten, they are merciful. -- The Tempest",
    "What's past is prologue. -- The Tempest",
    "Misery acquaints a man with strange bedfellows. -- The Tempest",
    "We are three men of sin, whom Destiny hath cast upon this shore. -- The Tempest",
    "This thing of darkness I acknowledge mine. -- The Tempest",
    "Full fathom five thy father lies; of his bones are coral made. -- The Tempest",
    "To be, or not to be, that is the question. -- Hamlet",
    "There is nothing either good or bad, but thinking makes it so. -- Hamlet",
    "This above all: to thine own self be true. -- Hamlet",
    "The rest is silence. -- Hamlet",
    "There are more things in heaven and earth, Horatio, than are dreamt of in your philosophy. -- Hamlet",
    "Though this be madness, yet there is method in't. -- Hamlet",
    "Brevity is the soul of wit. -- Hamlet",
    "The lady doth protest too much, methinks. -- Hamlet",
    "What a piece of work is a man! -- Hamlet",
    "Something is rotten in the state of Denmark. -- Hamlet",
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


def generate_caption(state_dir: str = ".") -> str:
    """
    Pick a random Shakespeare quote from Hamlet or The Tempest, excluding
    whichever quote was used last time (tracked via a small state file).
    """
    state_file = os.path.join(state_dir, "last_quote.txt")

    last_quote = None
    if os.path.isfile(state_file):
        with open(state_file, "r", encoding="utf-8") as f:
            last_quote = f.read().strip()

    choices = [q for q in SHAKESPEARE_QUOTES if q != last_quote] or SHAKESPEARE_QUOTES
    chosen = random.choice(choices)

    with open(state_file, "w", encoding="utf-8") as f:
        f.write(chosen)

    return chosen


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


def fetch_nasa_apod(output_path: str, api_key: str, max_attempts: int = 10) -> str:
    """
    Fetch NASA's Astronomy Picture of the Day, walking backward from today if
    a given day is a video (not an image) or otherwise unusable, and return
    an attribution string.
    """
    import datetime

    date = datetime.date.today()

    for _ in range(max_attempts):
        resp = requests.get(
            NASA_APOD_URL,
            params={"api_key": api_key, "date": date.isoformat()},
            timeout=30,
        )
        if resp.status_code != 200:
            date -= datetime.timedelta(days=1)
            continue

        data = resp.json()

        if data.get("media_type") != "image":
            date -= datetime.timedelta(days=1)
            continue

        image_url = data.get("hdurl") or data.get("url")
        if not image_url:
            date -= datetime.timedelta(days=1)
            continue

        image_resp = requests.get(image_url, timeout=60)
        if image_resp.status_code != 200:
            date -= datetime.timedelta(days=1)
            continue

        with open(output_path, "wb") as f:
            f.write(image_resp.content)

        title = data.get("title") or "Untitled"
        credit = data.get("copyright", "").strip() or "NASA"
        attribution = f"{title}, {date.isoformat()}\nImage credit: {credit} / NASA APOD"
        return attribution

    raise RuntimeError(f"Could not find a usable NASA APOD image after {max_attempts} attempts")


def fetch_artic_artwork(output_path: str, max_attempts: int = 100) -> str:
    """
    Search the Art Institute of Chicago's public API for a public-domain
    abstract/geometric artwork, download its image, and return an attribution string.
    """
    query = random.choice(ARTIC_SEARCH_TERMS)
    search_resp = requests.get(
        f"{ARTIC_API_BASE}/search",
        params={
            "q": query,
            "query[term][is_public_domain]": "true",
            "fields": "id,title,artist_display,date_display,image_id,is_public_domain",
            "limit": 100,
        },
        timeout=30,
    )
    search_resp.raise_for_status()
    results = search_resp.json().get("data") or []

    if not results:
        raise RuntimeError(f"No Art Institute of Chicago results found for query '{query}'")

    random.shuffle(results)

    for artwork in results[:max_attempts]:
        if not artwork.get("is_public_domain") or not artwork.get("image_id"):
            continue

        image_url = f"https://www.artic.edu/iiif/2/{artwork['image_id']}/full/843,/0/default.jpg"
        image_resp = requests.get(
            image_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.artic.edu/",
            },
            timeout=60,
        )
        if image_resp.status_code != 200:
            continue

        with open(output_path, "wb") as f:
            f.write(image_resp.content)

        title = artwork.get("title") or "Untitled"
        artist = artwork.get("artist_display") or "Unknown artist"
        date = artwork.get("date_display") or ""
        attribution = f"{title} -- {artist}" + (f", {date}" if date else "")
        attribution += "\nThe Art Institute of Chicago, Open Access (CC0)"
        return attribution

    raise RuntimeError(f"Could not find a usable public-domain image for query '{query}' after {max_attempts} attempts")


def _is_usable_license(license_short_name: str) -> bool:
    """Return True for public domain / CC0 / CC-BY / CC-BY-SA licenses; reject NC, ND, and anything unrecognized."""
    normalized = license_short_name.strip().lower().replace(" ", "-")
    if not normalized:
        return False

    tokens = normalized.split("-")
    if "nc" in tokens or "nd" in tokens:
        return False

    if normalized.startswith("cc0") or normalized.startswith("public-domain") or normalized in ("pd", "pdm"):
        return True

    return normalized.startswith("cc-by")


def _strip_html(text: str) -> str:
    """Strip HTML tags from Wikimedia extmetadata fields (e.g. Artist, Credit)."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def fetch_wikimedia_image(output_path: str, max_attempts: int = 25) -> str:
    """
    Search Wikimedia Commons for a public-domain / CC-licensed image matching a
    configurable search term, download it, and return an attribution string.
    """
    query = random.choice(WIKIMEDIA_SEARCH_TERMS)
    search_resp = requests.get(
        WIKIMEDIA_API_BASE,
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srnamespace": 6,
            "srlimit": max_attempts,
            "format": "json",
        },
        headers=WIKIMEDIA_HEADERS,
        timeout=30,
    )
    search_resp.raise_for_status()
    results = search_resp.json().get("query", {}).get("search") or []

    if not results:
        raise RuntimeError(f"No Wikimedia Commons results found for query '{query}'")

    random.shuffle(results)

    for result in results:
        title = result.get("title")
        if not title:
            continue

        info_resp = requests.get(
            WIKIMEDIA_API_BASE,
            params={
                "action": "query",
                "titles": title,
                "prop": "imageinfo",
                "iiprop": "url|mime|extmetadata",
                "format": "json",
            },
            headers=WIKIMEDIA_HEADERS,
            timeout=30,
        )
        if info_resp.status_code != 200:
            continue

        pages = info_resp.json().get("query", {}).get("pages") or {}
        page = next(iter(pages.values()), {})
        imageinfo = (page.get("imageinfo") or [None])[0]
        if not imageinfo:
            continue

        if not imageinfo.get("mime", "").startswith("image/"):
            continue

        extmetadata = imageinfo.get("extmetadata") or {}
        license_short = extmetadata.get("LicenseShortName", {}).get("value", "")
        if not _is_usable_license(license_short):
            continue

        image_url = imageinfo.get("url")
        if not image_url:
            continue

        image_resp = requests.get(image_url, headers=WIKIMEDIA_HEADERS, timeout=60)
        if image_resp.status_code != 200:
            continue

        with open(output_path, "wb") as f:
            f.write(image_resp.content)

        clean_title = re.sub(r"^File:", "", title).rsplit(".", 1)[0]
        artist = _strip_html(extmetadata.get("Artist", {}).get("value", "")) or "Unknown artist"
        attribution = f"{clean_title} -- {artist}"
        attribution += f"\nWikimedia Commons ({license_short or 'Public domain'})"
        return attribution

    raise RuntimeError(f"Could not find a usable licensed image for query '{query}' among {len(results)} results")


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
    valid_sources = ("stability", "met", "nasa", "artic", "commons")
    if len(sys.argv) not in (1, 2) or (len(sys.argv) == 2 and sys.argv[1] not in valid_sources):
        print(f"Usage: python post_to_instagram.py [{'|'.join(valid_sources)}]", file=sys.stderr)
        print("  If omitted, a source is chosen at random.", file=sys.stderr)
        sys.exit(1)

    source = sys.argv[1] if len(sys.argv) == 2 else random.choice(valid_sources)

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

        elif source == "nasa":
            nasa_key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
            print("Fetching NASA's Astronomy Picture of the Day...")
            attribution = fetch_nasa_apod(image_path, nasa_key)
            print(f"  -> {attribution}")

        elif source == "artic":
            print("Fetching a public-domain artwork from the Art Institute of Chicago...")
            attribution = fetch_artic_artwork(image_path)
            print(f"  -> {attribution}")

        elif source == "commons":
            print("Fetching a licensed image from Wikimedia Commons...")
            attribution = fetch_wikimedia_image(image_path)
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
