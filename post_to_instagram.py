#!/usr/bin/env python3
"""
Upload an image to Cloudinary and publish it to Instagram via the Graph API.

Usage:
    python post_to_instagram.py [stability|met|nasa|artic|commons]
    python post_to_instagram.py --story
    python post_to_instagram.py --auto
    python post_to_instagram.py --auto-story

    If a source is omitted, one is chosen at random each run (feed posts only).

    --story posts a single Instagram Story instead of a feed post: it picks
    one random image from the source rotation, excluding any image already
    used in that day's feed posts (tracked in used_images.log), and publishes
    it with no caption (Stories don't support captions via the Graph API).

    --auto / --auto-story are used by the scheduled GitHub Actions workflows,
    which run every 15 minutes rather than at 5 fixed times a day. Each
    invocation checks how long it's been since the last successful post and
    only actually posts once enough time has elapsed, exiting immediately
    otherwise -- so a dropped/delayed cron tick doesn't cost that slot for
    the whole day.
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
import inspect
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

VALID_SOURCES = ("stability", "met", "nasa", "artic", "commons", "lorenz")
USED_IMAGES_LOG = "used_images.log"

ULYSSES_QUOTES = [
    "History is a nightmare from which I am trying to awake. -- Ulysses",
    "Love loves to love love. -- Ulysses",
    "A man of genius makes no mistakes. His errors are volitional and are the portals of discovery. -- Ulysses",
    "Think you're escaping and run into yourself. Longest way round is the shortest way home. -- Ulysses",
    "Yes I said yes I will Yes. -- Ulysses",
    "Signatures of all things I am here to read, seaspawn and seawrack, the nearing tide, that rusty boot. -- Ulysses",
    "Ineluctable modality of the visible. -- Ulysses",
    "Beauty: it curves, curves are beauty. Shapely goddesses, Venus, Juno: curves the world admires. -- Ulysses",
    "Horseness is the whatness of allhorse. -- Ulysses",
    "Mr Leopold Bloom ate with relish the inner organs of beasts and fowls. -- Ulysses",
    "Stately, plump Buck Mulligan came from the stairhead, bearing a bowl of lather on which a mirror and a razor lay crossed. -- Ulysses",
    "O Jamesy let me up out of this. -- Ulysses",
    "I was happier then. Or was that I? Or am I now I? Can't bring back time. Like holding water in your hand. -- Ulysses",
    "What is that word known to all men? -- Ulysses",
    "Every life is in many days, day after day. We walk through ourselves, meeting robbers, ghosts, giants, old men, young men, wives, widows, brothers-in-love, but always meeting ourselves. -- Ulysses",
    "The heaventree of stars hung with humid nightblue fruit. -- Ulysses",
    "We mustn't be led away by words, by sounds of words. -- Ulysses",
    "Hold to the now, the here, through which all future plunges to the past. -- Ulysses",
    "I love flowers Id love to have the whole place swimming in roses God of heaven theres nothing like nature. -- Ulysses",
]

ELIOT_QUOTES = [
    "April is the cruellest month, breeding lilacs out of the dead land, mixing memory and desire, stirring dull roots with spring rain. -- The Waste Land",
    "I will show you fear in a handful of dust. -- The Waste Land",
    "These fragments I have shored against my ruins. -- The Waste Land",
    "You know only a heap of broken images, where the sun beats, and the dead tree gives no shelter, the cricket no relief, and the dry stone no sound of water. -- The Waste Land",
    "Unreal city, under the brown fog of a winter dawn, a crowd flowed over London Bridge, so many, I had not thought death had undone so many. -- The Waste Land",
    "O you who turn the wheel and look to windward, consider Phlebas, who was once handsome and tall as you. -- The Waste Land",
    "Datta. Dayadhvam. Damyata. Shantih shantih shantih. -- The Waste Land",
    "I think we are in rats' alley where the dead men lost their bones. -- The Waste Land",
    "The Chair she sat in, like a burnished throne, glowed on the marble. -- The Waste Land",
    "Madame Sosostris, famous clairvoyante, had a bad cold, nevertheless is known to be the wisest woman in Europe, with a wicked pack of cards. -- The Waste Land",
    "Oed' und leer das Meer. -- The Waste Land",
    "Those are pearls that were his eyes. -- The Waste Land",
    "I sat upon the shore fishing, with the arid plain behind me. Shall I at least set my lands in order? -- The Waste Land",
    "In the mountains, there you feel free. -- The Waste Land",
    "You gave me hyacinths first a year ago; they called me the hyacinth girl. -- The Waste Land",
    "Phlebas the Phoenician, a fortnight dead, forgot the cry of gulls, and the deep sea swell and the profit and loss. -- The Waste Land",
    "Who is the third who walks always beside you? -- The Waste Land",
    "London Bridge is falling down falling down falling down. -- The Waste Land",
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


FEED_POST_INTERVAL = datetime.timedelta(hours=4, minutes=40)
STORY_POST_INTERVAL = datetime.timedelta(hours=20)
LAST_FEED_POST_FILE = "last_feed_post.txt"
LAST_STORY_POST_FILE = "last_story_post.txt"


def _read_last_run_time(state_file: str):
    """Return the UTC datetime stored in state_file, or None if missing/unreadable."""
    if not os.path.isfile(state_file):
        return None
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            value = datetime.datetime.fromisoformat(f.read().strip())
    except (ValueError, OSError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value


def _write_last_run_time(state_file: str) -> None:
    with open(state_file, "w", encoding="utf-8") as f:
        f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())


def _is_due(state_file: str, interval: datetime.timedelta) -> bool:
    """
    True if state_file has no recorded timestamp yet, or enough time has
    elapsed since the last one. Used to make posting frequency resilient to
    GitHub Actions occasionally dropping an exact-time cron trigger: rather
    than each of 5 fixed daily cron times being a single point of failure,
    a lightweight check runs every 15 minutes and self-corrects by posting
    as soon as the interval has elapsed, whenever that turns out to be.
    """
    last = _read_last_run_time(state_file)
    if last is None:
        return True
    return (datetime.datetime.now(datetime.timezone.utc) - last) >= interval

def generate_caption(state_dir: str = ".") -> str:
    """
    Pick a random verbatim quote from James Joyce's Ulysses (public domain in the US), excluding
    whichever quote was used last time (tracked via a small state file).
    """
    state_file = os.path.join(state_dir, "last_quote.txt")

    last_quote = None
    if os.path.isfile(state_file):
        with open(state_file, "r", encoding="utf-8") as f:
            last_quote = f.read().strip()

    all_quotes = ULYSSES_QUOTES + ELIOT_QUOTES
    choices = [q for q in all_quotes if q != last_quote] or all_quotes
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



# Color and aesthetic are chosen independently and combined per run, so output
# varies meaningfully across posts instead of converging on one look (Claude
# defaulting to blue-toned "technical" imagery when no color was specified).
STABILITY_COLOR_PALETTES = [
    "monochrome red and black -- deep blood-red tones against black, no other colors",
    "deep green and black -- dark forest/emerald green tones against black, no other colors",
    "amber and black -- warm amber-orange glow against black, no other colors",
    "stark white and black -- high-contrast black and white only, no color at all",
    "cyan and black -- electric cyan tones against black, no other colors",
    "muted rust and ochre earth tones against near-black -- warm, weathered, desaturated",
]
# Earth tones are meant to show up only occasionally, not as often as the rest.
STABILITY_COLOR_WEIGHTS = [2, 2, 2, 2, 2, 1]

STABILITY_AESTHETIC_STYLES = [
    "instanced grid array of wireframe cubes at varying scale and rotation, like a "
    "TouchDesigner instancing output -- thin geometric wireframe structure",

    "smooth fluid organic forms -- flowing curves, soft undulating surfaces, "
    "liquid-like morphing shapes, no hard edges",

    "sharp architectural brutalist shapes -- massive angular concrete-like blocks "
    "and monoliths, dramatic hard shadows, monumental geometric forms",

    "sparse 3D point cloud or dense particle field suggesting a coordinate system "
    "or star field, faint thin axis lines, small monospace number labels near some "
    "points as if plotted from a dataset",

    "glitch and digital distortion -- abstract composition with scan-line artifacts, "
    "pixel sorting, data-corruption glitch effects, subtle digital noise",
]


def generate_image_prompt(api_key: str) -> str:
    """
    Generate a Stability AI prompt combining one randomly chosen color palette and
    one randomly chosen aesthetic/style direction, selected independently of each
    other, so output varies meaningfully across posts instead of converging on one
    look. The overall dark/minimal/moody tone stays constant; only color and
    stylistic treatment vary.
    """
    color = random.choices(STABILITY_COLOR_PALETTES, weights=STABILITY_COLOR_WEIGHTS)[0]
    aesthetic = random.choice(STABILITY_AESTHETIC_STYLES)
    return call_claude(
        api_key,
        f"Write one Stable Diffusion image generation prompt (max 40 words) for an "
        f"abstract generative-art composition combining this aesthetic direction: "
        f"{aesthetic}. Color palette: {color}. Keep the overall feel dark, minimal, "
        f"moody and restrained, not busy. Illegible or abstract glyph-like marks are "
        f"fine instead of real numbers where applicable -- clarity of numbers isn't "
        f"important, the technical/data-readout feeling is what matters when present. "
        f"No logos. Use only the specified color palette -- do not introduce any "
        f"other colors. Return only the prompt itself, nothing else.",
        max_tokens=120,
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


# Each entry: (label, ODE function factory, base initial state, t_span, num points)
# The ODE function takes (t, state) -> [dx, dy, dz], matching solve_ivp's signature.

def _lorenz_system(sigma=10, rho=28, beta=8 / 3):
    def f(t, state):
        x, y, z = state
        return [sigma * (y - x), x * (rho - z) - y, x * y - beta * z]
    return f


def _rossler_system(a=0.2, b=0.2, c=5.7):
    def f(t, state):
        x, y, z = state
        return [-y - z, x + a * y, b + z * (x - c)]
    return f


def _chua_system(alpha=15.6, beta=28, m0=-1.143, m1=-0.714):
    def diode(x):
        # Piecewise-linear approximation of Chua's diode nonlinearity.
        return m1 * x + 0.5 * (m0 - m1) * (abs(x + 1) - abs(x - 1))

    def f(t, state):
        x, y, z = state
        dx = alpha * (y - x - diode(x))
        dy = x - y + z
        dz = -beta * y
        return [dx, dy, dz]
    return f


def _aizawa_system(a=0.95, b=0.7, c=0.6, d=3.5, e=0.25, f=0.1):
    def fn(t, state):
        x, y, z = state
        dx = (z - b) * x - d * y
        dy = d * x + (z - b) * y
        dz = c + a * z - (z ** 3) / 3 - (x ** 2 + y ** 2) * (1 + e * z) + f * z * x ** 3
        return [dx, dy, dz]
    return fn

CHAOTIC_SYSTEMS = [
    {
        "label": "Lorenz attractor",
        "make_system": _lorenz_system,
        "base_state": (1.0, 1.0, 1.0),
        "perturbation": 0.5,
        "t_span": (0, 50),
        "num_points": 20000,
    },
    {
        "label": "Rössler attractor",
        "make_system": _rossler_system,
        "base_state": (1.0, 1.0, 1.0),
        "perturbation": 0.5,
        "t_span": (0, 200),
        "num_points": 20000,
    },
    {
        "label": "Chua's circuit attractor",
        "make_system": _chua_system,
        "base_state": (0.7, 0.0, 0.0),
        "perturbation": 0.05,
        "t_span": (0, 100),
        "num_points": 20000,
    },
    {
        "label": "Aizawa attractor",
        "make_system": _aizawa_system,
        "base_state": (0.1, 0.0, 0.0),
        "perturbation": 0.05,
        "t_span": (0, 100),
        "num_points": 20000,
    },]

# Two-color, slow-to-fast velocity gradients. On a dark background, slow blends
# toward the background (deep red, low contrast) and fast pops (white). On a
# light background that logic is inverted so fast still reads clearly: slow
# blends toward the background (pale pink) and fast darkens/saturates (deep red).
LORENZ_VELOCITY_PALETTES_DARK_BG = [
    ["#8B0000", "#FFFFFF"],  # deep red -> white
    ["#003B36", "#00F5FF"],  # deep teal -> cyan
]
LORENZ_VELOCITY_PALETTES_LIGHT_BG = [
    ["#F5D6D6", "#8B0000"],  # pale pink -> deep red
    ["#D6F5F5", "#003B36"],  # pale cyan -> deep teal
]
LORENZ_BACKGROUND_CHOICES = ["black", "white"]
LORENZ_BACKGROUND_WEIGHTS = [0.7, 0.3]

# Opacities for the 2-3 overlaid nearby-initial-condition trajectories, in order.
LORENZ_RUN_ALPHAS = [1.0, 0.6, 0.3]

# Display symbols for each system's parameter names, for the technical caption line.
CHAOTIC_PARAM_SYMBOLS = {
    "sigma": "σ", "rho": "ρ", "beta": "β", "alpha": "α",
    "m0": "m₀", "m1": "m₁",
    "a": "a", "b": "b", "c": "c", "d": "d", "e": "e", "f": "f",
}


def _system_parameters(make_system) -> dict:
    """The {name: default_value} parameters actually used to construct a chaotic
    system function (introspected from its signature, so this can't drift out
    of sync with the real defaults in _lorenz_system/_rossler_system/etc.)."""
    return {name: param.default for name, param in inspect.signature(make_system).parameters.items()}


def _format_number(value) -> str:
    rounded = round(value, 2)
    return f"{rounded:g}"


def format_attractor_technical_line(params: dict, initial_state) -> str:
    """
    Format the exact parameters and initial conditions used for a render as a
    short, precise technical line, e.g. "σ=10, ρ=28, β=2.67 -- initial state (1.03, 0.98, 1.01)".
    Values are the real numbers used, not paraphrased, so this reads as
    documentation of the actual generative process rather than decoration.
    """
    param_str = ", ".join(f"{CHAOTIC_PARAM_SYMBOLS.get(name, name)}={_format_number(value)}" for name, value in params.items())
    state_str = ", ".join(_format_number(v) for v in initial_state)
    return f"{param_str} -- initial state ({state_str})"


def generate_lorenz_attractor(output_path: str) -> tuple:
    """
    Pick one of four chaotic systems (Lorenz, Rössler, Chua's circuit, or Aizawa)
    at random, numerically integrate 2-3 trajectories from slightly perturbed
    nearby initial conditions, and render them overlaid as a dark, minimal 3D
    line plot -- no axes, no grid, just the trajectories, each colored along
    its length by local velocity magnitude (slow = deep red, fast = white) and
    each drawn at a different opacity. Every run produces a genuinely unique
    image (different system + initial conditions + camera angle), not a
    repeated/cached one.

    Returns (label, technical_line): the system's display name, and a short
    line reporting the exact parameter values and the first trajectory's
    initial conditions used for this specific render (see
    format_attractor_technical_line), for use in the post's caption.
    """
    import numpy as np
    from scipy.integrate import solve_ivp
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from mpl_toolkits.mplot3d.art3d import Line3DCollection

    system_spec = random.choice(CHAOTIC_SYSTEMS)
    system_params = _system_parameters(system_spec["make_system"])
    system_fn = system_spec["make_system"]()
    base_state = system_spec["base_state"]
    perturbation = system_spec["perturbation"]
    t_span = system_spec["t_span"]
    t_eval = np.linspace(*t_span, system_spec["num_points"])

    background = random.choices(LORENZ_BACKGROUND_CHOICES, weights=LORENZ_BACKGROUND_WEIGHTS)[0]
    velocity_colors = random.choice(
        LORENZ_VELOCITY_PALETTES_LIGHT_BG if background == "white" else LORENZ_VELOCITY_PALETTES_DARK_BG
    )

    cmap = LinearSegmentedColormap.from_list("velocity", velocity_colors)

    num_runs = random.choice([2, 3])
    elev = random.uniform(10, 35)
    azim = random.uniform(0, 360)

    fig = plt.figure(figsize=(10, 10), facecolor=background)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor(background)

    bounds_min = np.array([np.inf, np.inf, np.inf])
    bounds_max = np.array([-np.inf, -np.inf, -np.inf])
    primary_initial_state = None

    for run_index in range(num_runs):
        # Small random perturbation around the base starting point, so each
        # overlaid trajectory diverges from a nearby but distinct initial state.
        initial_state = [
            base_state[i] + random.uniform(-perturbation, perturbation)
            for i in range(3)
        ]
        if run_index == 0:
            primary_initial_state = tuple(initial_state)

        sol = solve_ivp(system_fn, t_span, initial_state, t_eval=t_eval)
        x, y, z = sol.y

        # Local velocity magnitude at every sampled point, from the system's own
        # derivative -- not a finite-difference approximation of the trace.
        derivatives = np.array([system_fn(t, state) for t, state in zip(sol.t, sol.y.T)])
        speed = np.linalg.norm(derivatives, axis=1)

        points = np.array([x, y, z]).T.reshape(-1, 1, 3)
        segments = np.concatenate([points[:-1], points[1:]], axis=1)
        segment_speed = (speed[:-1] + speed[1:]) / 2

        lc = Line3DCollection(
            segments,
            cmap=cmap,
            norm=Normalize(vmin=segment_speed.min(), vmax=segment_speed.max()),
            linewidth=0.6,
            alpha=LORENZ_RUN_ALPHAS[run_index],
        )
        lc.set_array(segment_speed)
        ax.add_collection3d(lc)

        bounds_min = np.minimum(bounds_min, [x.min(), y.min(), z.min()])
        bounds_max = np.maximum(bounds_max, [x.max(), y.max(), z.max()])

    ax.set_xlim(bounds_min[0], bounds_max[0])
    ax.set_ylim(bounds_min[1], bounds_max[1])
    ax.set_zlim(bounds_min[2], bounds_max[2])
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
    ax.grid(False)

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, facecolor=background, bbox_inches="tight", pad_inches=0)
    plt.close(fig)

    technical_line = format_attractor_technical_line(system_params, primary_initial_state)
    return system_spec["label"], technical_line


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

    if source == "lorenz":
        print("Generating a chaotic attractor...")
        system_label, technical_line = generate_lorenz_attractor(image_path)
        print(f"  -> saved to {image_path} ({system_label})")
        print(f"  -> {technical_line}")
        attribution = f"Generative -- {system_label} (chaotic system)\n{technical_line}"
        return attribution, None

    raise ValueError(f"Unknown source: {source}")


def run_feed_post(explicit_source: str = None):
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


def auto_feed():
    """
    Called every 15 minutes by the scheduled workflow. Posts a feed image only
    if FEED_POST_INTERVAL has elapsed since the last successful post, so a
    dropped/delayed GitHub Actions cron tick doesn't cost that slot for the
    whole day -- the next tick that fires will simply catch up.
    """
    if not _is_due(LAST_FEED_POST_FILE, FEED_POST_INTERVAL):
        last = _read_last_run_time(LAST_FEED_POST_FILE)
        remaining = FEED_POST_INTERVAL - (datetime.datetime.now(datetime.timezone.utc) - last)
        print(f"Not due yet -- last feed post was {last.isoformat()}, next one in ~{remaining}.")
        return

    run_feed_post()
    _write_last_run_time(LAST_FEED_POST_FILE)


def auto_story():
    """Same due-check pattern as auto_feed(), but for the once-daily Story post."""
    if not _is_due(LAST_STORY_POST_FILE, STORY_POST_INTERVAL):
        last = _read_last_run_time(LAST_STORY_POST_FILE)
        remaining = STORY_POST_INTERVAL - (datetime.datetime.now(datetime.timezone.utc) - last)
        print(f"Not due yet -- last Story post was {last.isoformat()}, next one in ~{remaining}.")
        return

    post_story()
    _write_last_run_time(LAST_STORY_POST_FILE)


def main():
    """
    CLI entry point.

        python post_to_instagram.py                 post a feed image now (random source)
        python post_to_instagram.py <source>         post a feed image now from a specific source
        python post_to_instagram.py --story           post a Story now (random source)
        python post_to_instagram.py --auto             post a feed image only if it's due (used by cron)
        python post_to_instagram.py --auto-story       post a Story only if it's due (used by cron)
    """
    args = sys.argv[1:]

    if args == ["--auto"]:
        auto_feed()
    elif args == ["--auto-story"]:
        auto_story()
    elif args == ["--story"]:
        post_story()
    elif len(args) == 0:
        run_feed_post()
    elif len(args) == 1 and args[0] in VALID_SOURCES:
        run_feed_post(explicit_source=args[0])
    else:
        print(
            f"Usage: python post_to_instagram.py [{'|'.join(VALID_SOURCES)}|--story|--auto|--auto-story]",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()