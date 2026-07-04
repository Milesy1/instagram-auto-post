#!/usr/bin/env python3
"""
Upload an image to Cloudinary and publish it to Instagram via the Graph API.

Usage:
    python post_to_instagram.py [stability|met|nasa|artic|commons]
    python post_to_instagram.py --story

    If a source is omitted, one is chosen at random each run (feed posts only).

    --story posts a single Instagram Story instead of a feed post: it picks
    one random image from the source rotation, excluding any image already
    used in that day's feed posts (tracked in used_images.log), and publishes
    it with no caption (Stories don't support captions via the Graph API).

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
import datetime
import requests

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.instagram.com/{GRAPH_API_VERSION}"

MET_API_BASE = "https://collectionapi.metmuseum.org/public/collection/v1"
MET_SEARCH_TERMS = ["abstract", "geometric abstraction", "cubism", "constructivism", "de stijl"]

NASA_APOD_URL = "https://api.nasa.gov/planetary/apod"

ARTIC_API_BASE = "https://api.artic.edu/api/v1/artworks"
ARTIC_SEARCH_TERMS = ["abstract", "geometric abstraction", "cubism", "constructivism", "de stijl", "minimalism"]

WIKIMEDIA_API_BASE = "https://commons.wikimedia.org/w/api.php"
WIKIMEDIA_SEARCH_TERMS = [
    "geometric abstract art",
    "wireframe",
    "monochrome pattern",
    "mathematical diagram",
    "vector field illustration",
    "topology diagram",
    "strange attractor",
    "fractal art",
    "Lorenz attractor",
    "Mandelbrot set",
    "Julia set fractal",
    "bifurcation diagram",
    "cellular automaton",
    "phase portrait",
    "phase space diagram",
    "chaos theory diagram",
    "dynamical system",
    "fractal flame",
]
WIKIMEDIA_CATEGORY_BLOCKLIST = ("archaeolog", "pottery", "museum object", "ceramic", "ancient")
WIKIMEDIA_PREFERRED_CATEGORIES = (
    "mathematical diagram",
    "fractal art",
    "computer-generated imagery",
    "abstract art",
    "chaos theory",
    "dynamical systems",
    "attractors",
    "fractals",
)
WIKIMEDIA_HEADERS = {"User-Agent": "instagram-auto-post/1.0 (https://github.com/milesy1/instagram-auto-post)"}

VALID_SOURCES = ("stability", "met", "nasa", "artic", "commons")
USED_IMAGES_LOG = "used_images.log"

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
    "Be not afeard; the isle is full of noises, sounds and sweet airs that give delight and hurt not. -- The Tempest",
    "Our revels now are ended. -- The Tempest",
    "How beauteous mankind is! O brave new world. -- The Tempest",
    "You taught me language, and my profit on't is, I know how to curse. -- The Tempest",
    "Now my charms are all o'erthrown, and what strength I have's mine own. -- The Tempest",
    "I would fain die a dry death. -- The Tempest",
    "Deeper than did ever plummet sound, I'll drown my book. -- The Tempest",
    "Where the bee sucks, there suck I; in a cowslip's bell I lie. -- The Tempest",
    "This is as strange a maze as e'er men trod. -- The Tempest",
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
    "Give every man thy ear, but few thy voice. -- Hamlet",
    "The play's the thing wherein I'll catch the conscience of the king. -- Hamlet",
    "Doubt thou the stars are fire; doubt that the sun doth move; but never doubt I love. -- Hamlet",
    "When sorrows come, they come not single spies, but in battalions. -- Hamlet",
    "Alas, poor Yorick! I knew him, Horatio. -- Hamlet",
    "Frailty, thy name is woman. -- Hamlet",
    "I must be cruel, only to be kind. -- Hamlet",
    "There's a divinity that shapes our ends, rough-hew them how we will. -- Hamlet",
    "The time is out of joint. O cursed spite, that ever I was born to set it right! -- Hamlet",
    "Words, words, words. -- Hamlet",
    "Neither a borrower nor a lender be. -- Hamlet",
    "This is the very ecstasy of love. -- Hamlet",
    "Conscience does make cowards of us all. -- Hamlet",
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


def load_used_images_today(state_dir: str = ".") -> set:
    """
    Return the set of source image URLs already posted today, based on the
    used-images log that both the feed and Stories flows append to after
    each successful post.
    """
    log_file = os.path.join(state_dir, USED_IMAGES_LOG)
    today = datetime.date.today().isoformat()

    used = set()
    if os.path.isfile(log_file):
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 3 and parts[0] == today:
                    used.add(parts[2])
    return used


def record_used_image(source: str, image_url: str, state_dir: str = ".") -> None:
    """
    Append a (date, source, image_url) record for today, pruning any records
    from earlier days so the log doesn't grow unbounded. No-op if image_url
    is falsy (e.g. the "stability" source, which generates rather than
    drawing from a trackable pool).
    """
    log_file = os.path.join(state_dir, USED_IMAGES_LOG)
    today = datetime.date.today().isoformat()

    lines = []
    if os.path.isfile(log_file):
        with open(log_file, "r", encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f if line.strip()]

    lines = [line for line in lines if line.split("\t", 1)[0] == today]
    if image_url:
        lines.append(f"{today}\t{source}\t{image_url}")

    with open(log_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


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


def fetch_met_artwork(output_path: str, max_attempts: int = 15, excluded_urls: frozenset = frozenset()) -> tuple:
    """
    Search the Met Museum's Open Access API for a public-domain abstract/geometric
    artwork, download its image, and return (attribution, image_url).
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

        if image_url in excluded_urls:
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
        return attribution, image_url

    raise RuntimeError(f"Could not find a usable public-domain image for query '{query}' after {max_attempts} attempts")


def fetch_nasa_apod(output_path: str, api_key: str, max_attempts: int = 10, excluded_urls: frozenset = frozenset()) -> tuple:
    """
    Fetch NASA's Astronomy Picture of the Day, walking backward from today if
    a given day is a video (not an image) or otherwise unusable, and return
    (attribution, image_url).
    """
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

        if image_url in excluded_urls:
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
        return attribution, image_url

    raise RuntimeError(f"Could not find a usable NASA APOD image after {max_attempts} attempts")


def fetch_artic_artwork(output_path: str, max_attempts: int = 100, excluded_urls: frozenset = frozenset()) -> tuple:
    """
    Search the Art Institute of Chicago's public API for a public-domain
    abstract/geometric artwork, download its image, and return (attribution, image_url).
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

    skipped_no_image = 0
    skipped_excluded = 0
    download_failures = {}

    for artwork in results[:max_attempts]:
        if not artwork.get("is_public_domain") or not artwork.get("image_id"):
            skipped_no_image += 1
            continue

        image_url = f"https://www.artic.edu/iiif/2/{artwork['image_id']}/full/843,/0/default.jpg"
        if image_url in excluded_urls:
            skipped_excluded += 1
            continue

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
            download_failures[image_resp.status_code] = download_failures.get(image_resp.status_code, 0) + 1
            continue

        with open(output_path, "wb") as f:
            f.write(image_resp.content)

        title = artwork.get("title") or "Untitled"
        artist = artwork.get("artist_display") or "Unknown artist"
        date = artwork.get("date_display") or ""
        attribution = f"{title} -- {artist}" + (f", {date}" if date else "")
        attribution += "\nThe Art Institute of Chicago, Open Access (CC0)"
        return attribution, image_url

    raise RuntimeError(
        f"Could not find a usable public-domain image for query '{query}' after {max_attempts} attempts "
        f"(not public domain/no image_id: {skipped_no_image}, already excluded: {skipped_excluded}, "
        f"download failures by status code: {download_failures or 'none'})"
    )


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


def _category_names(page: dict) -> list:
    """Lowercased Commons category titles for a page, e.g. 'category:fractal art'."""
    return [c.get("title", "").lower() for c in (page.get("categories") or [])]


def _has_blocklisted_category(categories: list) -> bool:
    return any(keyword in cat for cat in categories for keyword in WIKIMEDIA_CATEGORY_BLOCKLIST)


def _has_preferred_category(categories: list) -> bool:
    return any(keyword in cat for cat in categories for keyword in WIKIMEDIA_PREFERRED_CATEGORIES)


def fetch_wikimedia_image(output_path: str, max_attempts: int = 25, excluded_urls: frozenset = frozenset()) -> tuple:
    """
    Search Wikimedia Commons for a public-domain / CC-licensed image matching a
    configurable search term, download it, and return (attribution, image_url).

    Results are excluded if their Commons categories match WIKIMEDIA_CATEGORY_BLOCKLIST
    (e.g. archaeology/pottery/museum objects), and preferred if they match
    WIKIMEDIA_PREFERRED_CATEGORIES (e.g. mathematical diagrams, fractal art), so the
    subject matter stays consistent with the account's dark/minimal/geometric aesthetic.
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

    preferred_candidates = []
    fallback_candidates = []

    for result in results:
        title = result.get("title")
        if not title:
            continue

        info_resp = requests.get(
            WIKIMEDIA_API_BASE,
            params={
                "action": "query",
                "titles": title,
                "prop": "imageinfo|categories",
                "iiprop": "url|mime|extmetadata",
                "cllimit": "max",
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

        if image_url in excluded_urls:
            continue

        categories = _category_names(page)
        if _has_blocklisted_category(categories):
            continue

        candidate = (title, image_url, license_short, extmetadata)
        if _has_preferred_category(categories):
            preferred_candidates.append(candidate)
        else:
            fallback_candidates.append(candidate)

    for title, image_url, license_short, extmetadata in preferred_candidates + fallback_candidates:
        image_resp = requests.get(image_url, headers=WIKIMEDIA_HEADERS, timeout=60)
        if image_resp.status_code != 200:
            continue

        with open(output_path, "wb") as f:
            f.write(image_resp.content)

        clean_title = re.sub(r"^File:", "", title).rsplit(".", 1)[0]
        artist = _strip_html(extmetadata.get("Artist", {}).get("value", "")) or "Unknown artist"
        attribution = f"{clean_title} -- {artist}"
        attribution += f"\nWikimedia Commons ({license_short or 'Public domain'})"
        return attribution, image_url

    raise RuntimeError(f"Could not find a usable licensed image for query '{query}' among {len(results)} results")


def get_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def _parse_json_response(resp, error_prefix: str) -> dict:
    """
    Parse a response body as JSON, preferring the API's own structured error
    message (an "error" key) over a bare HTTP status. resp.raise_for_status()
    alone would raise on a 4xx/5xx before this code ever reads the body,
    hiding the actual reason the API gave (e.g. bad image dimensions).
    """
    try:
        result = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise

    if "error" in result:
        raise RuntimeError(f"{error_prefix}: {result['error']}")

    resp.raise_for_status()
    return result


def upload_to_cloudinary(image_path: str, cloud_name: str, upload_preset: str) -> str:
    """
    Upload image to Cloudinary (unsigned) and return a public JPEG URL, padded
    to a 4:5 aspect ratio (letterboxed in black rather than cropped, so no
    content is lost). Instagram rejects images outside roughly 4:5-1.91:1,
    and source images -- NASA APOD in particular, which is often panoramic --
    can fall outside that range.
    """
    url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"

    with open(image_path, "rb") as f:
        files = {"file": f}
        data = {"upload_preset": upload_preset}
        resp = requests.post(url, files=files, data=data, timeout=60)

    result = _parse_json_response(resp, "Cloudinary upload failed")
    secure_url = result["secure_url"]

    # Force JPEG delivery and pad to a safe aspect ratio in one transformation,
    # inserted right after "/upload/".
    transformed_url = secure_url.replace("/upload/", "/upload/f_jpg,c_pad,ar_4:5,b_black/", 1)

    return transformed_url


def create_media_container(ig_user_id: str, access_token: str, image_url: str, caption: str = None, media_type: str = None) -> str:
    """
    Create an Instagram media container and return its creation_id.

    caption is omitted for Stories (media_type="STORIES"), since the Graph API
    doesn't support captions on that media type.
    """
    url = f"{GRAPH_BASE}/{ig_user_id}/media"
    data = {
        "image_url": image_url,
        "access_token": access_token,
    }
    if caption:
        data["caption"] = caption
    if media_type:
        data["media_type"] = media_type

    resp = requests.post(url, data=data, timeout=60)
    result = _parse_json_response(resp, "Container creation failed")
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
    result = _parse_json_response(resp, "Publish failed")
    return result["id"]


def _fetch_for_source(source: str, image_path: str, excluded_urls: frozenset = frozenset()) -> tuple:
    """
    Fetch/generate an image for a single source, returning (attribution, image_source_url).
    Both are None for "stability" (generated fresh, not drawn from a trackable pool).
    Raises RuntimeError if the source has no usable image available (e.g. everything
    it found is already in excluded_urls).
    """
    if source == "stability":
        anthropic_key = get_env("ANTHROPIC_API_KEY")
        stability_key = get_env("STABILITY_API_KEY")

        print("Generating image prompt...")
        image_prompt = generate_image_prompt(anthropic_key)
        print(f"  -> {image_prompt}")

        print("Generating image via Stability AI...")
        generate_image_via_stability(image_prompt, stability_key, image_path)
        print(f"  -> saved to {image_path}")
        return None, None

    if source == "met":
        print("Fetching a public-domain artwork from the Met...")
        attribution, image_source_url = fetch_met_artwork(image_path, excluded_urls=excluded_urls)
        print(f"  -> {attribution}")
        return attribution, image_source_url

    if source == "nasa":
        nasa_key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
        print("Fetching NASA's Astronomy Picture of the Day...")
        attribution, image_source_url = fetch_nasa_apod(image_path, nasa_key, excluded_urls=excluded_urls)
        print(f"  -> {attribution}")
        return attribution, image_source_url

    if source == "artic":
        print("Fetching a public-domain artwork from the Art Institute of Chicago...")
        attribution, image_source_url = fetch_artic_artwork(image_path, excluded_urls=excluded_urls)
        print(f"  -> {attribution}")
        return attribution, image_source_url

    if source == "commons":
        print("Fetching a licensed image from Wikimedia Commons...")
        attribution, image_source_url = fetch_wikimedia_image(image_path, excluded_urls=excluded_urls)
        print(f"  -> {attribution}")
        return attribution, image_source_url

    raise ValueError(f"Unknown source: {source}")


def main():
    if len(sys.argv) not in (1, 2) or (len(sys.argv) == 2 and sys.argv[1] not in VALID_SOURCES):
        print(f"Usage: python post_to_instagram.py [{'|'.join(VALID_SOURCES)}]", file=sys.stderr)
        print("  If omitted, a source is chosen at random.", file=sys.stderr)
        sys.exit(1)

    explicit_source = sys.argv[1] if len(sys.argv) == 2 else None

    cloud_name = get_env("CLOUDINARY_CLOUD_NAME")
    upload_preset = get_env("CLOUDINARY_UPLOAD_PRESET")
    ig_user_id = get_env("IG_USER_ID")
    access_token = get_env("IG_ACCESS_TOKEN")

    print("Selecting caption...")
    quote = generate_caption()
    print(f"  -> {quote}")

    # An explicitly requested source (e.g. a manual test run) is tried once and
    # fails hard with no fallback. Otherwise (the normal scheduled-run case),
    # try the rotation in random order and fall through to the next one if a
    # source has nothing usable, or if Cloudinary/Instagram rejects the
    # fetched image for any reason (e.g. an unsupported aspect ratio), rather
    # than failing the run outright.
    sources = [explicit_source] if explicit_source else random.sample(VALID_SOURCES, len(VALID_SOURCES))
    last_error = None

    with tempfile.TemporaryDirectory() as tmpdir:
        image_path = os.path.join(tmpdir, "image.jpg")

        for source in sources:
            print(f"Source: {source}")
            try:
                attribution, image_source_url = _fetch_for_source(source, image_path)

                caption = quote
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
            except RuntimeError as exc:
                print(f"  -> unavailable: {exc}")
                last_error = exc
                if explicit_source:
                    raise
                continue

            record_used_image(source, image_source_url)
            return

    raise RuntimeError(f"Could not publish a post from any source: {last_error}")


def post_story():
    """
    Post a single Instagram Story: pick one random image from the source
    rotation, excluding any source image already used in today's feed posts,
    and publish it with media_type=STORIES (no caption).

    Tries sources in random order and falls through to the next one if a
    source has nothing to offer, or if Cloudinary/Instagram rejects the
    fetched image for any reason (e.g. an unsupported aspect ratio), rather
    than failing the run outright.
    """
    cloud_name = get_env("CLOUDINARY_CLOUD_NAME")
    upload_preset = get_env("CLOUDINARY_UPLOAD_PRESET")
    ig_user_id = get_env("IG_USER_ID")
    access_token = get_env("IG_ACCESS_TOKEN")

    excluded_urls = load_used_images_today()
    print(f"Excluding {len(excluded_urls)} image(s) already used in today's feed posts.")

    sources = random.sample(VALID_SOURCES, len(VALID_SOURCES))
    last_error = None

    with tempfile.TemporaryDirectory() as tmpdir:
        image_path = os.path.join(tmpdir, "image.jpg")

        for source in sources:
            print(f"Trying source: {source}")
            try:
                attribution, image_source_url = _fetch_for_source(source, image_path, excluded_urls=excluded_urls)

                print("Uploading image to Cloudinary...")
                image_url = upload_to_cloudinary(image_path, cloud_name, upload_preset)
                print(f"  -> {image_url}")

                print("Creating Instagram Story media container...")
                creation_id = create_media_container(ig_user_id, access_token, image_url, media_type="STORIES")
                print(f"  -> creation_id: {creation_id}")

                print("Waiting for container to finish processing...")
                wait_for_container_ready(creation_id, access_token)
                print("  -> FINISHED")

                print("Publishing Story to Instagram...")
                media_id = publish_media(ig_user_id, access_token, creation_id)
                print(f"  -> Published! Media ID: {media_id}")
            except RuntimeError as exc:
                print(f"  -> unavailable: {exc}")
                last_error = exc
                continue

            record_used_image(source, image_source_url)
            return

    raise RuntimeError(f"Could not publish a Story from any source: {last_error}")


if __name__ == "__main__":
    if "--story" in sys.argv:
        post_story()
    else:
        main()
