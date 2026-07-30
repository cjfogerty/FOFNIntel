#!/usr/bin/env python3
"""
Log in to the Barron Swim School (O'Fallon, MO) Jackrabbit Class parent portal and
pull the full class catalog as JSON.

Unlike the iClass Pro competitors (build_iclass_dashboard.py), Barron's Jackrabbit
portal requires an authenticated parent account -- there's no public API. This script
drives a real login through Playwright (username/password from env vars, never
hardcoded or logged), then makes one authenticated fetch() from inside the page to
the portal's own GetClassesForEnroll endpoint, which returns clean structured JSON
(instructor names, categories, per-weekday meeting flags, wait-list status) -- far
richer than the site's own CSV export.

Env vars required:
  BARRON_EMAIL     -- parent portal login email / user id
  BARRON_PASSWORD  -- parent portal login password

Usage: python3 barron_extract.py [--out barron_raw.json]
"""
import argparse
import json
import os
import sys

from playwright.sync_api import sync_playwright

ORG_ID = "544652"
LOGIN_URL = f"https://app.jackrabbitclass.com/jr4.0/ParentPortal/Login?OrgID={ORG_ID}"
API_PATH = f"/jr4.0/ParentPortal/GetClassesForEnroll?OrgID={ORG_ID}"


def fetch_classes(page):
    result = page.evaluate(
        """(path) => fetch(path, {
            method: 'POST',
            credentials: 'include',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({})
        }).then(r => r.json())""",
        API_PATH,
    )
    if not result.get("success"):
        raise RuntimeError("GetClassesForEnroll did not report success: %r" % result.get("message"))
    return result["data"]["classes"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "barron_raw.json"))
    a = ap.parse_args()

    email = os.environ.get("BARRON_EMAIL")
    password = os.environ.get("BARRON_PASSWORD")
    if not email or not password:
        sys.exit("BARRON_EMAIL and BARRON_PASSWORD must be set in the environment")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(LOGIN_URL, wait_until="networkidle")
        page.fill("#UserName", email)
        page.fill("#Password", password)
        page.click("button:has-text('SIGN IN')")
        page.wait_for_load_state("networkidle")

        if "Login" in page.url:
            browser.close()
            sys.exit("Login did not redirect away from the login page -- check credentials")

        classes = fetch_classes(page)
        browser.close()

    with open(a.out, "w") as f:
        json.dump(classes, f)
    print("wrote %s (%d classes)" % (a.out, len(classes)))


if __name__ == "__main__":
    main()
