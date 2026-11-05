#!/usr/bin/env python3
"""Fetch InstiApp (IIT Bombay) blog entries the same way the Flutter app does.

Mirrors lib/src/blocs/blog_bloc.dart + lib/src/api/apiclient.dart:
  GET https://gymkhana.iitb.ac.in/instiapp/api/{placement,training,external}-blog
      ?from=<offset>&num=<page size>&query=<search>
  Cookie: sessionid=<session id>

Auth (pick one):
  --session SID   an existing InstiApp session id (or env INSTIAPP_SESSION)
  --code CODE     an OAuth code from the gymkhana SSO redirect; it is exchanged
                  via GET /login?code=..&redir=.. exactly like the app's login page.
                  Get one by opening --print-login-url in a browser, logging in,
                  and copying ?code=... from the insti.app/login-android.html URL.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://gymkhana.iitb.ac.in/instiapp/api"
REDIRECT_URI = "https://www.insti.app/login-android.html"
LOGIN_URL = (
    "https://gymkhana.iitb.ac.in/profiles/oauth/authorize/"
    "?client_id=vR1pU7wXWyve1rUkg0fMS6StL1Kr6paoSmRIiLXJ&response_type=code"
    "&scope=basic%20profile%20picture%20sex%20ldap%20phone%20insti_address"
    "%20program%20secondary_emails&redirect_uri=" + REDIRECT_URI
)
ENDPOINTS = {
    "placement": "/placement-blog",
    "training": "/training-blog",  # "Internship blog" in the UI
    "external": "/external-blog",
}
PAGE_SIZE = 20  # PostBloc._noOfPostsPerPage


def api_get(path, params=None, session=None):
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if session:
        req.add_header("Cookie", f"sessionid={session}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        hint = ""
        if path == "/login" and "required fields" in body:
            hint = ("\nThe code was accepted, but your SSO profile lacks first_name, "
                    "roll_number or username. Grant every permission on the consent "
                    "screen and retry with a fresh code (each code works only once).")
        sys.exit(f"HTTP {e.code} for {url}\n{body}{hint}")
    except (urllib.error.URLError, OSError) as e:
        # Timeouts, refused/reset connections, DNS failures (HTTPError is handled above).
        reason = getattr(e, "reason", None) or e
        sys.exit(f"InstiApp API not responding ({type(reason).__name__}: {reason}) for {url}")


def login_with_code(code):
    session = api_get("/login", {"code": code, "redir": REDIRECT_URI})
    sid = session.get("sessionid")
    if not sid:
        sys.exit(f"Login failed: {session}")
    print(f"Logged in as {session.get('user')}; session id: {sid}", file=sys.stderr)
    return sid


def fetch_page(blog, session, offset, num, query):
    return api_get(
        ENDPOINTS[blog],
        {"from": offset, "num": num, "query": query},
        session,
    )


def fetch(blog, session, offset, num, query, fetch_all):
    if not fetch_all:
        return fetch_page(blog, session, offset, num, query)
    posts = []
    while True:
        page = fetch_page(blog, session, offset, num, query)
        posts.extend(page)
        # The app treats a short page as the end of the feed.
        if len(page) < num:
            return posts
        offset += num


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("blog", nargs="?", choices=ENDPOINTS, default="placement")
    p.add_argument("--session", default=os.environ.get("INSTIAPP_SESSION"))
    p.add_argument("--code", help="OAuth code to exchange for a session")
    p.add_argument("--print-login-url", action="store_true")
    p.add_argument("--query", default="", help="server-side search (app sends it at >=4 chars)")
    p.add_argument("--from", dest="offset", type=int, default=0)
    p.add_argument("--num", type=int, default=PAGE_SIZE)
    p.add_argument("--all", action="store_true", help="page through the whole feed")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.add_argument("--content", action="store_true", help="also print post content (markdown)")
    args = p.parse_args()

    if args.print_login_url:
        print(LOGIN_URL)
        return

    session = login_with_code(args.code) if args.code else args.session
    if not session:
        p.error("need --session SID (or INSTIAPP_SESSION) or --code CODE; "
                "see --print-login-url")

    posts = fetch(args.blog, session, args.offset, args.num, args.query, args.all)

    if args.json:
        json.dump(posts, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return
    for post in posts:
        print(f"[{post.get('published', '')}] {post.get('title', '')}")
        print(f"  {post.get('link', '')}")
        if args.content:
            print("  " + (post.get("content") or "").strip().replace("\n", "\n  "))
        print()
    print(f"{len(posts)} post(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
