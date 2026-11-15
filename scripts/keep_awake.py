"""
Keep the free Streamlit Community Cloud app from staying asleep.

Free apps go to sleep after a period with no visitors. A plain HTTP request
doesn't count as a visit, so this opens the app in a real (headless) browser,
presses "Yes, get this app back up!" if the sleep page is showing, and waits
until BakeCost has actually loaded. Run on a schedule by
.github/workflows/keep-awake.yml.

Exits with an error if the app doesn't load, so GitHub emails the repo owner.
"""

from __future__ import annotations

import os
import re
import sys

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

APP_URL = os.environ.get("APP_URL", "https://bakecost.streamlit.app")
WAKE_BUTTON = re.compile(r"get this app back up", re.IGNORECASE)
APP_READY_TEXT = "Ingredients"          # a label on the BakeCost dashboard
LOAD_TIMEOUT_MS = 4 * 60 * 1000         # waking from sleep can take a couple of minutes


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(APP_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(5_000)

        button = page.get_by_role("button", name=WAKE_BUTTON)
        if button.count() and button.first.is_visible():
            print("App was asleep - pressing the wake-up button.")
            button.first.click()
        else:
            print("App is awake (or starting).")

        # Streamlit Cloud shows the app inside an iframe.
        try:
            page.frame_locator("iframe").first.get_by_text(APP_READY_TEXT).first.wait_for(timeout=LOAD_TIMEOUT_MS)
        except PlaywrightTimeout:
            print(f"BakeCost did not load at {APP_URL} within {LOAD_TIMEOUT_MS // 1000} s.")
            browser.close()
            return 1
        print("BakeCost loaded successfully.")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
