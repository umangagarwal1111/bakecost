"""Small helpers shared by every page (not a page itself)."""

from __future__ import annotations

import os
from contextlib import contextmanager

import streamlit as st

import seed
from modules import db, units
from modules.matching import Candidate

# Units a baker may pick, grouped by what the ingredient is stored in.
UNITS_FOR_BASE = {
    "g": ["g", "kg", "cup", "tbsp", "tsp"],
    "ml": ["ml", "l", "cup", "tbsp", "tsp"],
    "pcs": ["pcs", "dozen"],
}
ALL_RECIPE_UNITS = ["g", "kg", "ml", "l", "cup", "tbsp", "tsp", "pcs", "dozen"]
BASE_UNIT_LABELS = {"g": "Weight (g / kg)", "ml": "Volume (ml / L)", "pcs": "Count (pieces)"}
YIELD_UNITS = ["kg", "g", "pcs", "dozen", "l", "ml"]


def demo_mode() -> bool:
    """True when the app is hosted publicly for people to try (BAKECOST_DEMO=1).

    On a shared server every visitor would otherwise use the SAME database
    file, so one tester pressing "Load demo data" would wipe everyone else's
    work. In demo mode each browser session gets its own private in-memory
    database instead, pre-filled with the demo data.
    """
    return os.environ.get("BAKECOST_DEMO", "").strip().lower() in ("1", "true", "yes")


def get_conn():
    """One SQLite connection per browser session, created on first use."""
    if "conn" not in st.session_state:
        if demo_mode():
            conn = db.connect(":memory:")       # private to this visitor, gone when they leave
            seed.load_demo_data(conn)
            st.session_state.conn = conn
        else:
            st.session_state.conn = db.connect()
    return st.session_state.conn


def inr(amount: float | None, decimals: int = 2) -> str:
    """₹1,234.50 - Indian users expect the rupee sign and thousands separators."""
    if amount is None:
        return "—"
    sign = "-" if amount < 0 else ""
    return f"{sign}₹{abs(amount):,.{decimals}f}"


def price_label(price_per_base: float | None, base_unit: str) -> str:
    shown, per = units.price_for_display(price_per_base, base_unit)
    return "no price yet" if shown is None else f"{inr(shown)}/{per}"


@contextmanager
def friendly_errors(action: str = "That didn't work"):
    """Show expected problems (bad input) as a friendly message instead of a
    stack trace. Unexpected bugs still surface so they get fixed."""
    try:
        yield
    except (ValueError, units.UnitConversionError) as exc:
        st.error(f"{action}: {exc}")


def candidates(conn) -> list[Candidate]:
    """The ingredient library in the shape the matcher wants."""
    return [Candidate(i["id"], i["name"], db.split_aliases(i["aliases"])) for i in db.list_ingredients(conn)]


def empty_library_hint(conn) -> bool:
    """If there are no ingredients yet, point the user at demo data. Returns True if empty."""
    if db.counts(conn)["ingredients"] == 0:
        st.info("Your ingredient library is empty. Add ingredients on the **Ingredients** page, "
                "or press **Load demo data** on the Dashboard to try the app.", icon="💡")
        return True
    return False


def flash(message: str, kind: str = "success") -> None:
    """Queue a message to show after st.rerun() (which would otherwise wipe it)."""
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    """Call near the top of a page to display a queued message once."""
    kind, message = st.session_state.pop("_flash", (None, None))
    if message:
        getattr(st, kind)(message)
