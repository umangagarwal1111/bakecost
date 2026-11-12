"""
SQLite storage for BakeCost.

This is the only module that writes SQL. Pages call these functions and get
back plain dicts, so the UI never needs to know table layouts.

Design choices
--------------
* sqlite3 from the standard library: a single file (data/bakecost.db), no
  server to install - right for a one-person business.
* Prices are stored per BASE unit (Rs per g / ml / pc). Costing is then a
  single multiplication; the UI converts to "Rs per kg" for display.
* Every price change also writes a PriceHistory row, so the baker can see
  how prices moved and where each number came from (bill scan or manual).
* Foreign keys are ON, so an ingredient used in a recipe can't be deleted
  by accident (the recipe would silently become cheaper).
* All queries use "?" placeholders - never string formatting - which
  prevents SQL injection and quoting bugs with names like "Baker's chocolate".
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bakecost.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredients (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    name                        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    aliases                     TEXT NOT NULL DEFAULT '',     -- comma separated
    base_unit                   TEXT NOT NULL CHECK (base_unit IN ('g', 'ml', 'pcs')),
    current_price_per_base_unit REAL CHECK (current_price_per_base_unit IS NULL
                                            OR current_price_per_base_unit >= 0),
    last_updated                TEXT,                          -- ISO date
    category                    TEXT NOT NULL DEFAULT 'Other'
);

CREATE TABLE IF NOT EXISTS price_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ingredient_id       INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
    price_per_base_unit REAL NOT NULL,
    quantity_bought     REAL,                                  -- in base units
    source              TEXT NOT NULL CHECK (source IN ('bill', 'manual')),
    date                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_ingredient ON price_history(ingredient_id, date);

CREATE TABLE IF NOT EXISTS recipes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    yield_quantity  REAL NOT NULL CHECK (yield_quantity > 0),
    yield_unit      TEXT NOT NULL,
    wastage_percent REAL NOT NULL DEFAULT 5 CHECK (wastage_percent >= 0),
    notes           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS recipe_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id     INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE RESTRICT,
    quantity      REAL NOT NULL CHECK (quantity > 0),
    unit          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_recipe ON recipe_items(recipe_id);

CREATE TABLE IF NOT EXISTS fixed_costs (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL,
    type   TEXT NOT NULL CHECK (type IN ('per_order', 'per_kg', 'percent_of_cost')),
    value  REAL NOT NULL CHECK (value >= 0),
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    number              TEXT NOT NULL UNIQUE,
    date                TEXT NOT NULL,
    customer_name       TEXT NOT NULL,
    customer_phone      TEXT NOT NULL DEFAULT '',
    line_items_json     TEXT NOT NULL,     -- what the customer sees
    cost_breakdown_json TEXT NOT NULL,     -- internal cost sheet (never on the invoice)
    subtotal            REAL NOT NULL,
    tax_amount          REAL NOT NULL,
    total               REAL NOT NULL
);
"""

# Every setting with its default. The type of the default decides how the
# stored text is converted back (float, int or str).
DEFAULT_SETTINGS: dict[str, object] = {
    "business_name": "My Home Bakery",
    "address": "",
    "phone": "",
    "gstin": "",
    "tax_label": "GST",
    "tax_percent": 0.0,          # deliberately 0: the baker sets their own rate
    "currency": "INR",
    "rounding_step": 5,          # round final price to nearest Rs 5 (or 10, or 1)
    "invoice_prefix": "INV-",
    "invoice_counter": 1,        # next number to issue
    "default_profit_mode": "margin",
    "default_profit_percent": 50.0,
}


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Open (and if needed create) the database.

    `path=":memory:"` gives a throw-away database, which the tests use.
    The BAKECOST_DB environment variable can point the app at another file.
    """
    path = path or os.environ.get("BAKECOST_DB") or DEFAULT_DB_PATH
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: Streamlit may run reruns of one session on
    # different threads (never at the same time), so this is safe here.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _today() -> str:
    return date.today().isoformat()


def _rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


def split_aliases(text: str | None) -> list[str]:
    """'Maida, all purpose flour , APF' -> ['Maida', 'all purpose flour', 'APF']"""
    return [a.strip() for a in (text or "").split(",") if a.strip()]


# ---------------------------------------------------------------------------
# Ingredients & price history
# ---------------------------------------------------------------------------


def list_ingredients(conn, search: str | None = None) -> list[dict]:
    """All ingredients, optionally filtered by name/alias/category text."""
    sql = "SELECT * FROM ingredients"
    params: tuple = ()
    if search:
        sql += " WHERE name LIKE ? OR aliases LIKE ? OR category LIKE ?"
        like = f"%{search.strip()}%"
        params = (like, like, like)
    return _rows(conn.execute(sql + " ORDER BY category, name", params))


def get_ingredient(conn, ingredient_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone()
    return dict(row) if row else None


def find_ingredient_by_name(conn, name: str) -> dict | None:
    row = conn.execute("SELECT * FROM ingredients WHERE name = ? COLLATE NOCASE", (name.strip(),)).fetchone()
    return dict(row) if row else None


def add_ingredient(
    conn,
    name: str,
    base_unit: str,
    price_per_base_unit: float | None = None,
    category: str = "Other",
    aliases: str = "",
    quantity_bought: float | None = None,
    source: str = "manual",
    on_date: str | None = None,
) -> int:
    """Create an ingredient; if a price is given it also starts the price history."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Ingredient name can't be empty.")
    if find_ingredient_by_name(conn, name):
        raise ValueError(f"'{name}' is already in your library.")
    with conn:  # one transaction: ingredient + first history row, or neither
        cur = conn.execute(
            "INSERT INTO ingredients (name, aliases, base_unit, category) VALUES (?, ?, ?, ?)",
            (name, aliases.strip(), base_unit, category.strip() or "Other"),
        )
        new_id = cur.lastrowid
        if price_per_base_unit is not None:
            _record_price(conn, new_id, price_per_base_unit, quantity_bought, source, on_date or _today())
    return new_id


def update_ingredient(conn, ingredient_id: int, name: str, aliases: str, category: str) -> None:
    """Edit descriptive fields. The base unit is fixed once created, because
    changing it would make every stored price and recipe quantity wrong."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Ingredient name can't be empty.")
    other = find_ingredient_by_name(conn, name)
    if other and other["id"] != ingredient_id:
        raise ValueError(f"Another ingredient is already called '{name}'.")
    with conn:
        conn.execute(
            "UPDATE ingredients SET name = ?, aliases = ?, category = ? WHERE id = ?",
            (name, aliases.strip(), category.strip() or "Other", ingredient_id),
        )


def _record_price(conn, ingredient_id, price, quantity_bought, source, on_date) -> None:
    if price is None or price < 0:
        raise ValueError("Price must be zero or more.")
    if source not in ("bill", "manual"):
        raise ValueError("Price source must be 'bill' or 'manual'.")
    conn.execute(
        "INSERT INTO price_history (ingredient_id, price_per_base_unit, quantity_bought, source, date)"
        " VALUES (?, ?, ?, ?, ?)",
        (ingredient_id, price, quantity_bought, source, on_date),
    )
    conn.execute(
        "UPDATE ingredients SET current_price_per_base_unit = ?, last_updated = ? WHERE id = ?",
        (price, on_date, ingredient_id),
    )


def update_price(conn, ingredient_id: int, price_per_base_unit: float, quantity_bought: float | None = None,
                 source: str = "manual", on_date: str | None = None) -> None:
    """Set a new current price and log it in the price history (atomically)."""
    with conn:
        _record_price(conn, ingredient_id, price_per_base_unit, quantity_bought, source, on_date or _today())


def price_history(conn, ingredient_id: int) -> list[dict]:
    return _rows(conn.execute(
        "SELECT * FROM price_history WHERE ingredient_id = ? ORDER BY date DESC, id DESC", (ingredient_id,)
    ))


def delete_ingredient(conn, ingredient_id: int) -> None:
    used_in = [r["name"] for r in conn.execute(
        "SELECT DISTINCT r.name FROM recipes r JOIN recipe_items i ON i.recipe_id = r.id"
        " WHERE i.ingredient_id = ?", (ingredient_id,))]
    if used_in:
        raise ValueError("Can't delete: it is used in " + ", ".join(used_in) + ". Remove it from those recipes first.")
    with conn:
        conn.execute("DELETE FROM ingredients WHERE id = ?", (ingredient_id,))


def list_categories(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT category FROM ingredients ORDER BY category")]


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------


def list_recipes(conn) -> list[dict]:
    return _rows(conn.execute(
        "SELECT r.*, COUNT(i.id) AS item_count FROM recipes r"
        " LEFT JOIN recipe_items i ON i.recipe_id = r.id GROUP BY r.id ORDER BY r.name"
    ))


def get_recipe(conn, recipe_id: int) -> dict | None:
    """A recipe with its items (each item joined with ingredient details)."""
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not row:
        return None
    recipe = dict(row)
    recipe["items"] = _rows(conn.execute(
        "SELECT i.id, i.ingredient_id, i.quantity, i.unit, g.name AS ingredient_name, g.base_unit,"
        "       g.current_price_per_base_unit, g.last_updated"
        " FROM recipe_items i JOIN ingredients g ON g.id = i.ingredient_id"
        " WHERE i.recipe_id = ? ORDER BY i.id", (recipe_id,)
    ))
    return recipe


def save_recipe(conn, name: str, yield_quantity: float, yield_unit: str, items: list[dict],
                wastage_percent: float = 5.0, notes: str = "", recipe_id: int | None = None) -> int:
    """Insert a new recipe (recipe_id=None) or replace an existing one.

    `items` is a list of {"ingredient_id", "quantity", "unit"}. Items are
    replaced wholesale inside one transaction: simpler than diffing rows and
    the recipe can never be left half-saved.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Recipe name can't be empty.")
    if not yield_quantity or yield_quantity <= 0:
        raise ValueError("Yield must be more than zero.")
    if not items:
        raise ValueError("Add at least one ingredient to the recipe.")
    clash = conn.execute("SELECT id FROM recipes WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if clash and clash["id"] != recipe_id:
        raise ValueError(f"A recipe called '{name}' already exists.")
    with conn:
        if recipe_id is None:
            recipe_id = conn.execute(
                "INSERT INTO recipes (name, yield_quantity, yield_unit, wastage_percent, notes) VALUES (?, ?, ?, ?, ?)",
                (name, yield_quantity, yield_unit, wastage_percent, notes),
            ).lastrowid
        else:
            conn.execute(
                "UPDATE recipes SET name = ?, yield_quantity = ?, yield_unit = ?, wastage_percent = ?, notes = ?"
                " WHERE id = ?", (name, yield_quantity, yield_unit, wastage_percent, notes, recipe_id),
            )
            conn.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe_id,))
        conn.executemany(
            "INSERT INTO recipe_items (recipe_id, ingredient_id, quantity, unit) VALUES (?, ?, ?, ?)",
            [(recipe_id, it["ingredient_id"], it["quantity"], it["unit"]) for it in items],
        )
    return recipe_id


def delete_recipe(conn, recipe_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))


# ---------------------------------------------------------------------------
# Fixed costs
# ---------------------------------------------------------------------------


def list_fixed_costs(conn, active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM fixed_costs" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
    return _rows(conn.execute(sql))


def replace_fixed_costs(conn, rows: list[dict]) -> None:
    """Save the whole fixed-cost table at once (matches the editable grid UI)."""
    for r in rows:
        if not str(r.get("name", "")).strip():
            raise ValueError("Every fixed cost needs a name.")
        if r.get("type") not in ("per_order", "per_kg", "percent_of_cost"):
            raise ValueError(f"'{r.get('name')}': choose a type (per_order, per_kg or percent_of_cost).")
        if r.get("value") is None or float(r["value"]) < 0:
            raise ValueError(f"'{r.get('name')}': value must be zero or more.")
    with conn:
        conn.execute("DELETE FROM fixed_costs")
        conn.executemany(
            "INSERT INTO fixed_costs (name, type, value, active) VALUES (?, ?, ?, ?)",
            [(r["name"].strip(), r["type"], float(r["value"]), 1 if r.get("active", True) else 0) for r in rows],
        )


# ---------------------------------------------------------------------------
# Settings (simple key/value table, typed through DEFAULT_SETTINGS)
# ---------------------------------------------------------------------------


def get_settings(conn) -> dict:
    stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    out = {}
    for key, default in DEFAULT_SETTINGS.items():
        raw = stored.get(key)
        if raw is None:
            out[key] = default
        elif isinstance(default, int) and not isinstance(default, bool):
            out[key] = int(float(raw))
        elif isinstance(default, float):
            out[key] = float(raw)
        else:
            out[key] = raw
    return out


def save_settings(conn, values: dict) -> None:
    unknown = set(values) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    with conn:
        conn.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k, str(v)) for k, v in values.items()],
        )


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


def _take_invoice_number(conn) -> str:
    """Read and bump the counter. Called inside the save transaction, so two
    invoices can never get the same number."""
    settings = get_settings(conn)
    counter = settings["invoice_counter"]
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('invoice_counter', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(counter + 1),)
    )
    return f"{settings['invoice_prefix']}{counter:04d}"


def save_invoice(conn, customer_name: str, customer_phone: str, line_items: list[dict], cost_breakdown: dict,
                 subtotal: float, tax_amount: float, total: float, on_date: str | None = None) -> dict:
    """Store an invoice and return it (with its newly issued number)."""
    if not (customer_name or "").strip():
        raise ValueError("Customer name is required for an invoice.")
    with conn:
        number = _take_invoice_number(conn)
        new_id = conn.execute(
            "INSERT INTO invoices (number, date, customer_name, customer_phone, line_items_json,"
            " cost_breakdown_json, subtotal, tax_amount, total) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (number, on_date or _today(), customer_name.strip(), (customer_phone or "").strip(),
             json.dumps(line_items), json.dumps(cost_breakdown), subtotal, tax_amount, total),
        ).lastrowid
    return get_invoice(conn, new_id)


def get_invoice(conn, invoice_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not row:
        return None
    inv = dict(row)
    inv["line_items"] = json.loads(inv.pop("line_items_json"))
    inv["cost_breakdown"] = json.loads(inv.pop("cost_breakdown_json"))
    return inv


def list_invoices(conn, limit: int = 20) -> list[dict]:
    return _rows(conn.execute(
        "SELECT id, number, date, customer_name, customer_phone, total FROM invoices ORDER BY id DESC LIMIT ?",
        (limit,),
    ))


# ---------------------------------------------------------------------------
# Dashboard helpers & housekeeping
# ---------------------------------------------------------------------------


def most_expensive_ingredients(conn, limit: int = 5) -> list[dict]:
    """Highest price per kg / L / pc. Prices are per g or ml, so x1000 puts
    weight and volume items on the familiar per-kg / per-L scale."""
    return _rows(conn.execute(
        "SELECT name, base_unit, current_price_per_base_unit,"
        "  current_price_per_base_unit * CASE base_unit WHEN 'pcs' THEN 1 ELSE 1000 END AS display_price,"
        "  last_updated FROM ingredients WHERE current_price_per_base_unit IS NOT NULL"
        " ORDER BY display_price DESC LIMIT ?", (limit,)
    ))


def counts(conn) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("ingredients", "recipes", "invoices", "fixed_costs")}


def clear_all(conn) -> None:
    """Delete every row (used before loading demo data). Settings are kept."""
    with conn:
        for table in ("recipe_items", "recipes", "price_history", "invoices", "fixed_costs", "ingredients"):
            conn.execute(f"DELETE FROM {table}")
