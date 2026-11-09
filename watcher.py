#!/usr/bin/env python3
"""Show only blog entries that appeared since the last run, optionally on Telegram.

Uses insti_blog.py for the API calls. Seen post ids are kept in
cache/seen-<blog>.json next to this script. The first run only records the current
posts, so later runs show just what's new.

  python3 watcher.py [placement|training|external] [--telegram]

Settings come from config.json next to this script (command-line flags and
INSTIAPP_SESSION override it):
  {"session": "<InstiApp session id>",
   "telegram_token": "<bot token from @BotFather>",
   "telegram_chat_id": "<your chat id, see --telegram-setup>"}
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from insti_blog import ENDPOINTS, PAGE_SIZE, fetch_page

# Kept next to this script (not the current directory), so it works from anywhere.
HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / "cache"
CONFIG = HERE / "config.json"
MAX_PAGES = 5  # stop paging after this many pages even if no seen post turns up
MAX_SEEN = 1000


def load_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def telegram(token, method, payload=None):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)["result"]
    except urllib.error.HTTPError as e:
        raise TelegramError(f"Telegram {method} failed: HTTP {e.code} "
                            f"{e.read().decode(errors='replace')[:300]}", e.code)


class TelegramError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def send(token, chat_ids, text):
    for chat_id in chat_ids:
        try:
            telegram(token, "sendMessage", {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
        except TelegramError as e:
            # 403: this user blocked the bot or it was removed from the group.
            # Skip them so everyone else still gets the post.
            if e.code != 403:
                raise TelegramError(f"chat {chat_id}: {e}", e.code) from e
            print(f"Skipping chat {chat_id}: {e}", file=sys.stderr)
        time.sleep(0.1)  # stay well under Telegram's rate limits on big backlogs


def alert(token, chat_ids, flag, text):
    """Send a problem notice once; the flag file stays until the problem clears."""
    if flag.exists():
        return
    try:
        send(token, chat_ids, text)
    except (TelegramError, OSError) as e:  # don't hide the original error behind this one
        print(f"Could not send Telegram alert: {e}", file=sys.stderr)
        return
    flag.touch()


class PostText(HTMLParser):
    """Turns a post's WordPress HTML into Telegram HTML (telegram=True) or plain text."""
    INLINE = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u",
              "s": "s", "strike": "s", "del": "s", "code": "code"}
    BLOCK = {"p", "div", "ul", "ol", "table", "tr", "blockquote", "pre",
             "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self, telegram):
        super().__init__(convert_charrefs=True)
        self.telegram = telegram
        self.out = []
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.out.append("\n")
        elif tag in self.BLOCK:
            self.out.append("\n\n")
        elif tag == "li":
            self.out.append("\n• ")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            self.hrefs.append(href)
            if self.telegram and href:
                self.out.append(f'<a href="{html.escape(href)}">')
        elif self.telegram and tag in self.INLINE:
            self.out.append(f"<{self.INLINE[tag]}>")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.out.append("\n\n")
        elif tag == "a":
            href = self.hrefs.pop() if self.hrefs else ""
            if href:
                self.out.append("</a>" if self.telegram else f" ({href})")
        elif self.telegram and tag in self.INLINE:
            self.out.append(f"</{self.INLINE[tag]}>")

    def handle_data(self, data):
        data = re.sub(r"\s+", " ", data)  # HTML whitespace, incl. &nbsp;
        self.out.append(html.escape(data, quote=False) if self.telegram else data)

    @classmethod
    def convert(cls, source, telegram=False):
        parser = cls(telegram)
        parser.feed(source or "")
        parser.close()
        text = re.sub(r" *\n *", "\n", "".join(parser.out))
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def pretty_time(stamp):
    """2026-10-26T13:40:21+05:30 -> Mon, 26 Oct 2026 · 1:40 PM (in the post's own timezone)."""
    try:
        dt = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return stamp or ""
    return f"{dt:%a}, {dt.day} {dt:%b %Y} · {dt.hour % 12 or 12}:{dt:%M %p}"


def format_post(blog, post):
    link = html.escape(post.get("link", ""))
    title = html.escape(post.get("title", ""))
    head = (f'<b><a href="{link}">{title}</a></b>\n'
            f"{html.escape(pretty_time(post.get('published')))} · {blog}\n"
            f"- - - - - - - - - - - -\n\n")
    text = head + PostText.convert(post.get("content"), telegram=True)
    if len(text) <= 4000:  # Telegram's limit is 4096 characters
        return text
    # Cutting Telegram HTML could leave a tag open, so shorten the plain text instead.
    return head + html.escape(PostText.convert(post.get("content"))[:3900 - len(head)]) + "…"


def setup_telegram(token):
    """Print the chat ids of people who recently messaged the bot."""
    chats = {}
    for update in telegram(token, "getUpdates"):
        # my_chat_member covers the bot being added to a group or channel.
        msg = (update.get("message") or update.get("channel_post")
               or update.get("my_chat_member") or {})
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")
    if not chats:
        sys.exit("No messages found. Send any message to your bot in Telegram, then rerun.")
    for chat_id, name in chats.items():
        print(f'{chat_id}\t{name}')
    print('Put the ones you want in config.json as "telegram_chat_id" '
          '(a list for several, e.g. [123, -100456]).', file=sys.stderr)


def main():
    cfg = load_json(CONFIG) or {}
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("blog", nargs="?", choices=ENDPOINTS, default="placement")
    p.add_argument("--session", default=os.environ.get("INSTIAPP_SESSION") or cfg.get("session"))
    p.add_argument("--telegram", action="store_true", help="send new posts to Telegram")
    p.add_argument("--telegram-setup", action="store_true",
                   help="list chat ids that messaged the bot, to fill in config.json")
    args = p.parse_args()

    token, chat_id = cfg.get("telegram_token"), cfg.get("telegram_chat_id")
    # One chat id or a list of them (several users, groups or channels).
    if isinstance(chat_id, str) and chat_id.strip().startswith("["):
        p.error(f'"telegram_chat_id" in {CONFIG} is a quoted string; '
                'write the list without quotes, e.g. [123, -456]')
    chat_id = [c for c in (chat_id if isinstance(chat_id, list) else [chat_id]) if c]
    if (args.telegram or args.telegram_setup) and not token:
        p.error(f'set "telegram_token" in {CONFIG}')
    if args.telegram_setup:
        return setup_telegram(token)
    if args.telegram and not chat_id:
        p.error(f'set "telegram_chat_id" in {CONFIG} (find it with --telegram-setup)')
    if not args.session:
        p.error(f'need --session SID, INSTIAPP_SESSION or "session" in {CONFIG}')

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = STATE_DIR / f"seen-{args.blog}.json"
    expired_flag = STATE_DIR / "session-expired"
    down_flag = STATE_DIR / "api-down"
    seen = load_json(state)
    seen_set = set(seen or [])

    # Page through the feed until we reach a post we've already seen.
    posts = []
    try:
        for page_no in range(1 if seen is None else MAX_PAGES):
            page = fetch_page(args.blog, args.session, page_no * PAGE_SIZE, PAGE_SIZE, "")
            posts.extend(page)
            if len(page) < PAGE_SIZE or any(post["id"] in seen_set for post in page):
                break
    except SystemExit as e:  # insti_blog.api_get exits on HTTP/network errors
        if args.telegram:
            if "HTTP 401" in str(e):
                alert(token, chat_id, expired_flag,
                      "⚠️ InstiApp session expired. Update \"session\" in config.json.")
            elif "not responding" in str(e):
                alert(token, chat_id, down_flag,
                      "⚠️ InstiApp API is not responding. Will keep retrying; "
                      "you'll get new posts once it's back.")
        raise
    expired_flag.unlink(missing_ok=True)
    down_flag.unlink(missing_ok=True)

    new = [post for post in posts if post["id"] not in seen_set]

    done = []  # newest first, like the feed
    try:
        if seen is not None:
            for post in reversed(new):  # oldest first, newest at the bottom
                if args.telegram:
                    send(token, chat_id, format_post(args.blog, post))
                done.insert(0, post["id"])
                print(f"[{pretty_time(post.get('published'))}] {post.get('title', '')}")
                print("  " + PostText.convert(post.get("content")).replace("\n", "\n  "))
                print()
        done = [post["id"] for post in posts]
    finally:
        # If a Telegram send fails, only the posts already sent count as seen,
        # so the rest are retried next run.
        ids = done + (seen or [])
        state.write_text(json.dumps(list(dict.fromkeys(ids))[:MAX_SEEN]))

    if seen is None:
        print(f"First run: recorded {len(posts)} {args.blog} post(s). "
              "Run again later to see new ones.", file=sys.stderr)
    else:
        print(f"{len(new)} new {args.blog} post(s)", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except TelegramError as e:
        sys.exit(str(e))
