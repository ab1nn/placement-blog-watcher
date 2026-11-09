# Placement Blog Notifier

Watches the IIT Bombay [InstiApp](https://www.insti.app) blogs (placement,
internship/training, external) and tells you about new posts, in the terminal
or on Telegram. It calls the same API the official InstiApp Flutter app uses.

Python 3 standard library only. There is nothing to install.

## Files

| File | Purpose |
| --- | --- |
| `watcher.py` | Shows only posts that are new since the last run; optionally sends them to Telegram. This is the one you run on a schedule. |
| `insti_blog.py` | API client and CLI: list or search blog posts, and get a session id from an SSO login code. `watcher.py` imports it. |
| `config.json` | Your session id and Telegram settings. |
| `cache/` | Created at runtime. Holds seen post ids and alert flags. |
| `placement-notifier-apk/` | A separate, unbuilt Android version of the same idea. See its own README. |

## Setup

### 1. Get an InstiApp session id

```sh
python3 insti_blog.py --print-login-url
```

Open the URL in a browser and log in with your IITB SSO. Grant every
permission on the consent screen. You will be redirected to
`insti.app/login-android.html?code=...`. Copy the `code` and exchange it:

```sh
python3 insti_blog.py --code <CODE>
```

The session id is printed to stderr. Codes work only once.

### 2. Create `config.json`

```json
{
  "session": "<InstiApp session id>",
  "telegram_token": "<bot token from @BotFather>",
  "telegram_chat_id": [123456789, -1001234567890]
}
```

`telegram_chat_id` is a single id or a list (users, groups, channels). Only
`session` is needed if you don't use Telegram. The session can also be passed
with `--session SID` or the `INSTIAPP_SESSION` environment variable, which
override the config file.

### 3. (Telegram only) Find your chat id

Create a bot with [@BotFather](https://t.me/BotFather), put its token in
`config.json`, send the bot any message (or add it to a group), then run:

```sh
python3 watcher.py --telegram-setup
```

Copy the ids you want into `telegram_chat_id`.

## Usage

```sh
python3 watcher.py                       # placement blog, print new posts
python3 watcher.py training              # internship blog
python3 watcher.py external --telegram   # external blog, also send to Telegram
```

The first run for each blog only records the current posts and prints nothing
new. Later runs show just what appeared since. Seen ids are stored per blog in
`cache/seen-<blog>.json` (last 1000 kept). If a Telegram send fails, only the
posts already sent are marked seen, so the rest are retried next run.

With `--telegram`, you also get a one-time alert when the session expires
(HTTP 401) or the API is down. The alert is not repeated until the problem
clears.

### Run it on a schedule

```cron
*/15 * * * * cd /path/to/placement-blog-notifier && python3 watcher.py --telegram
```

Use one entry per blog you want to follow.

## Browsing the API directly

```sh
python3 insti_blog.py placement --num 5            # latest 5 posts
python3 insti_blog.py placement --all --json       # whole feed as JSON
python3 insti_blog.py training --query "intern" --content
```

Run with `-h` for all options.

## Security

`config.json` holds a session id and bot token. The committed copy contains
placeholders only. Don't commit real values; consider adding `config.json` to
`.gitignore` or keeping the real one outside version control.
