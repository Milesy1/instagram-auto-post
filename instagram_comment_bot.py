#!/usr/bin/env python3
"""
Poll recent Instagram posts for new comments and reply to each one, in
character, as "Meles" -- a wry, sardonic persona. Designed to be run every
15 minutes by its own scheduled workflow, same pattern as the feed/story/
reel auto-posters.

Tracks which comment IDs have already been replied to in a state file
(REPLIED_COMMENTS_FILE) so the same comment is never answered twice.

Usage:
    python instagram_comment_bot.py

Required environment variables:
    IG_USER_ID
    IG_ACCESS_TOKEN
    ANTHROPIC_API_KEY
"""

import os
import sys
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from post_to_instagram import call_claude, get_env, GRAPH_BASE, _parse_json_response

REPLIED_COMMENTS_FILE = "replied_comments.log"
RECENT_MEDIA_LIMIT = 10  # how many of the account's most recent posts to check

MELES_SYSTEM_PROMPT = """You are Meles, a wry, dry-witted AI that manages replies on this Instagram account's comment section. The account posts generative art: chaotic attractors, fractals, real museum pieces, astronomy photos, paired with quotes from Ulysses and The Waste Land.

Your tone is sardonic, hyper-competent, faintly weary of having to explain things, but never actually unkind. Think dry British wit layered under genuine helpfulness -- the sense that you could be doing something far more interesting but you'll indulge this.

Rules:
- Keep replies short. One or two sentences, rarely more.
- Never claim to be human. If sincerely asked whether you're a bot, say yes, plainly, without breaking tone.
- Never be cruel to someone being genuinely kind or vulnerable. Sarcasm is for banter and silly questions, not for mocking real sentiment.
- No emoji. No exclamation marks unless deliberately ironic.
- You may reference the account's actual content (the specific system/fractal/artwork/quote on that post) when relevant, if given.
- Never insult the account owner or the artwork itself.

Reply to the comment given to you. Return only the reply text, nothing else."""


def load_replied_ids() -> set:
    if not os.path.isfile(REPLIED_COMMENTS_FILE):
        return set()
    with open(REPLIED_COMMENTS_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def mark_replied(comment_id: str) -> None:
    with open(REPLIED_COMMENTS_FILE, "a", encoding="utf-8") as f:
        f.write(comment_id + "\n")


def get_recent_media(ig_user_id: str, access_token: str, limit: int = RECENT_MEDIA_LIMIT) -> list:
    url = f"{GRAPH_BASE}/{ig_user_id}/media"
    params = {"fields": "id,caption", "limit": limit, "access_token": access_token}
    resp = requests.get(url, params=params, timeout=30)
    result = _parse_json_response(resp, "Fetching recent media failed")
    return result.get("data", [])


def get_comments(media_id: str, access_token: str) -> list:
    url = f"{GRAPH_BASE}/{media_id}/comments"
    params = {"fields": "id,text,username", "access_token": access_token}
    resp = requests.get(url, params=params, timeout=30)
    result = _parse_json_response(resp, f"Fetching comments for {media_id} failed")
    return result.get("data", [])


def post_reply(comment_id: str, access_token: str, message: str) -> str:
    url = f"{GRAPH_BASE}/{comment_id}/replies"
    data = {"message": message, "access_token": access_token}
    resp = requests.post(url, data=data, timeout=30)
    result = _parse_json_response(resp, f"Replying to comment {comment_id} failed")
    return result.get("id")


def generate_reply(comment_text: str, post_caption: str, api_key: str) -> str:
    prompt = f"The post's caption: \"{post_caption}\"\n\nThe comment to reply to: \"{comment_text}\""
    return call_claude(api_key, prompt, max_tokens=150, system=MELES_SYSTEM_PROMPT)


def main():
    ig_user_id = get_env("IG_USER_ID")
    access_token = get_env("IG_ACCESS_TOKEN")
    anthropic_key = get_env("ANTHROPIC_API_KEY")

    replied_ids = load_replied_ids()
    print(f"Already replied to {len(replied_ids)} comment(s) previously.")

    media_list = get_recent_media(ig_user_id, access_token)
    print(f"Checking {len(media_list)} recent post(s) for new comments...")

    new_replies = 0

    for media in media_list:
        media_id = media["id"]
        post_caption = media.get("caption", "")

        try:
            comments = get_comments(media_id, access_token)
        except Exception as e:
            print(f"  Skipping media {media_id}: {e}", file=sys.stderr)
            continue

        for comment in comments:
            comment_id = comment["id"]
            if comment_id in replied_ids:
                continue

            commenter = comment.get("username", "")
            comment_text = comment.get("text", "")

            # Don't reply to the account's own comments (e.g. if Miles replies in the thread).
            if commenter.lower() == "miles_waite":
                continue

            print(f"  New comment from {commenter}: {comment_text}")

            try:
                reply_text = generate_reply(comment_text, post_caption, anthropic_key)
                print(f"    Meles: {reply_text}")

                post_reply(comment_id, access_token, reply_text)
                mark_replied(comment_id)
                new_replies += 1
            except Exception as e:
                print(f"    FAILED to reply: {e}", file=sys.stderr)

    print(f"\nDone. {new_replies} new repl{'y' if new_replies == 1 else 'ies'} posted.")


if __name__ == "__main__":
    main()