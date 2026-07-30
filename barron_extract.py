#!/usr/bin/env python3
"""
Log in to a Barron (O'Fallon swim school / Ballwin sports center / ...) Jackrabbit
Class parent portal and pull the full class catalog as JSON.

Unlike the iClass Pro competitors (build_iclass_dashboard.py), Barron's Jackrabbit
portals require an authenticated parent account -- there's no public API. This
script drives a real login through Playwright (username/password from env vars,
never hardcoded or logged), then makes one authenticated fetch() from inside the
page to the portal's own GetClassesForEnroll endpoint, which returns clean
structured JSON (instructor names, categories, per-weekday meeting flags,
wait-list status) -- far richer than the site's own CSV export.

Each Barron location is a SEPARATE Jackrabbit org with its own login (same email
+ password across locations, per Casey, but the account itself must be created
per-location by signing up as a parent there first). The jr3.0/jr4.0 path in the
login URL doesn't matter -- both forward into the same jr4.0 app/API once given
an OrgID, so this always uses the jr4.0 login URL.

Env vars required:
  BARRON_EMAIL     -- parent portal login email / user id
  BARRON_PASSWORD  -- parent portal login password

Usage: python3 barron_extract.py --org-id 544652 --out barron_ofallon_raw.json
"""
import argparse
import json
import os
import sys

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ERROR_SELECTORS = ".validation-summary-errors, .field-validation-error, .alert-danger, [class*='error']"


def fetch_classes(page, org_id):
    api_path = f"/jr4.0/ParentPortal/GetClassesForEnroll?OrgID={org_id}"
    result = page.evaluate(
        """(path) => fetch(path, {
            method: 'POST',
            credentials: 'include',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({})
        }).then(r => r.json())""",
        api_path,
    )
    if not result.get("success"):
        raise RuntimeError("GetClassesForEnroll did not report success: %r" % result.get("message"))
    return result["data"]["classes"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-id", required=True, help="Jackrabbit OrgID for this Barron location")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "barron_raw.json"))
    a = ap.parse_args()

    login_url = f"https://app.jackrabbitclass.com/jr4.0/ParentPortal/Login?OrgID={a.org_id}"

    email = os.environ.get("BARRON_EMAIL")
    password = os.environ.get("BARRON_PASSWORD")
    if not email or not password:
        sys.exit("BARRON_EMAIL and BARRON_PASSWORD must be set in the environment")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(login_url, wait_until="domcontentloaded")
        page.wait_for_selector("#UserName", timeout=15000)
        page.fill("#UserName", email)
        page.fill("#Password", password)
        page.click("button:has-text('SIGN IN')")

        try:
            page.wait_for_url(lambda url: "Login" not in url, timeout=20000)
        except PlaywrightTimeoutError:
            errors = [
                t.strip()
                for t in page.locator(ERROR_SELECTORS).all_inner_texts()
                if t.strip()
            ]
            shot_path = f"barron_login_failure_{a.org_id}.png"
            try:
                page.screenshot(path=shot_path)
            except Exception:
                shot_path = None
            browser.close()
            detail = "; ".join(errors) if errors else "no on-page error text found"
            sys.exit(
                "Login did not redirect away from the login page after 20s (org %s). "
                "On-page message(s): %s. Screenshot: %s" % (a.org_id, detail, shot_path)
            )

        classes = fetch_classes(page, a.org_id)
        browser.close()

    with open(a.out, "w") as f:
        json.dump(classes, f)
    print("wrote %s (%d classes)" % (a.out, len(classes)))


if __name__ == "__main__":
    main()
