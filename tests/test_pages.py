"""Smoke test: every Streamlit page runs without an error, on an empty
database and on demo data. Uses Streamlit's built-in headless AppTest."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import seed
from modules import db

VIEWS = Path(__file__).resolve().parent.parent / "views"
PAGES = ["dashboard", "new_order", "ingredients", "recipes", "bill_scan", "recipe_scan", "settings"]


@pytest.fixture
def demo_db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("BAKECOST_DB", str(path))
    return path


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("with_demo", [False, True])
def test_page_renders(page, with_demo, demo_db):
    if with_demo:
        seed.load_demo_data(db.connect(demo_db))
    at = AppTest.from_file(str(VIEWS / f"{page}.py"), default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]


def test_new_order_creates_invoice(demo_db):
    seed.load_demo_data(db.connect(demo_db))
    at = AppTest.from_file(str(VIEWS / "new_order.py"), default_timeout=30).run()
    truffle = next(o for o in at.selectbox[0].options if "Truffle" in o)
    at.selectbox[0].select(truffle).run()
    at.text_input[0].input("Priya Sharma").run()
    at.button[0].click().run()
    assert not at.exception
    invoices = db.list_invoices(db.connect(demo_db))
    assert len(invoices) == 1 and invoices[0]["total"] % 10 == 0
    # the same order can't be invoiced twice by a double click
    assert at.button[0].disabled


def test_demo_mode_gives_each_visitor_private_demo_data(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKECOST_DEMO", "1")
    monkeypatch.setenv("BAKECOST_DB", str(tmp_path / "shared.db"))
    first = AppTest.from_file(str(VIEWS / "dashboard.py"), default_timeout=30).run()
    assert not first.exception
    assert first.metric[0].value == "25"                  # demo ingredients pre-loaded
    first.session_state.conn.execute("DELETE FROM recipe_items")
    second = AppTest.from_file(str(VIEWS / "dashboard.py"), default_timeout=30).run()
    assert second.session_state.conn.execute("SELECT COUNT(*) FROM recipe_items").fetchone()[0] > 0
    assert not (tmp_path / "shared.db").exists()          # nothing written to the shared file
