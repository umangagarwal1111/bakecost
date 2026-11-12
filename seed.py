"""
Demo data: 25 common Indian home-baking ingredients, 4 recipes, fixed costs
and business settings, so the app can be demoed without scanning anything.

Run from a terminal:   python seed.py          (asks before replacing data)
                       python seed.py --yes    (no question)
Or press "Load demo data" on the Dashboard page.

Prices are typical Indian retail prices in late 2026 (INR), entered the way
a baker thinks about them - per kg, per litre or per piece - and converted to
the per-gram / per-ml base unit by the same helper the app uses.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from modules import db
from modules.units import price_from_display

# (name, category, base_unit, price per kg / L / pc, aliases)
INGREDIENTS = [
    ("Maida", "Flours & Starches", "g", 45, "all purpose flour, apf, refined flour, plain flour, maida flour"),
    ("Whole Wheat Flour", "Flours & Starches", "g", 52, "atta, wheat flour, chakki atta"),
    ("Cornflour", "Flours & Starches", "g", 120, "corn flour, corn starch, cornstarch"),
    ("Cocoa Powder", "Flours & Starches", "g", 950, "cocoa, unsweetened cocoa, cocoa pwd"),
    ("Sugar", "Sugars & Sweeteners", "g", 48, "white sugar, granulated sugar, cheeni, sugar m30"),
    ("Icing Sugar", "Sugars & Sweeteners", "g", 110, "powdered sugar, confectioners sugar, sugar powder"),
    ("Brown Sugar", "Sugars & Sweeteners", "g", 160, "demerara, light brown sugar"),
    ("Condensed Milk", "Sugars & Sweeteners", "g", 360, "milkmaid, sweetened condensed milk"),
    ("Butter", "Dairy & Eggs", "g", 560, "unsalted butter, amul butter, white butter, table butter"),
    ("Milk", "Dairy & Eggs", "ml", 68, "toned milk, full cream milk, doodh, amul taaza"),
    ("Fresh Cream", "Dairy & Eggs", "ml", 230, "amul fresh cream, cooking cream, malai"),
    ("Whipping Cream", "Dairy & Eggs", "ml", 300, "non dairy whipping cream, tropolite, rich's"),
    ("Curd", "Dairy & Eggs", "g", 88, "dahi, yogurt, yoghurt"),
    ("Milk Powder", "Dairy & Eggs", "g", 450, "dairy whitener, everyday milk powder"),
    ("Eggs", "Dairy & Eggs", "pcs", 7, "egg, farm eggs, brown eggs"),
    ("Dark Compound Chocolate", "Chocolate & Nuts", "g", 400, "dark chocolate, compound chocolate, morde dark, dark compound"),
    ("Chocolate Chips", "Chocolate & Nuts", "g", 450, "choco chips, choc chips, chocolate chip"),
    ("Walnuts", "Chocolate & Nuts", "g", 1400, "akhrot, walnut kernels"),
    ("Cashews", "Chocolate & Nuts", "g", 1000, "kaju, cashew nuts, cashew"),
    ("Baking Powder", "Leavening & Flavour", "g", 500, "bp, weikfield baking powder"),
    ("Baking Soda", "Leavening & Flavour", "g", 150, "soda bicarb, sodium bicarbonate, cooking soda, meetha soda"),
    ("Salt", "Leavening & Flavour", "g", 28, "table salt, iodised salt, namak, tata salt"),
    ("Vanilla Essence", "Leavening & Flavour", "ml", 1500, "vanilla extract, vanilla"),
    ("Instant Coffee", "Leavening & Flavour", "g", 3200, "coffee, nescafe, coffee powder"),
    ("Vegetable Oil", "Oils & Fats", "ml", 150, "refined oil, sunflower oil, oil, cooking oil"),
]

# Ingredients whose last price is deliberately old, so the demo shows the
# "price older than 60 days" warning on the brownies.
STALE = {"Walnuts": 75}

# (name, yield qty, yield unit, wastage %, notes, [(ingredient, qty, unit), ...])
# Kitchen units (cup/tsp) are used on purpose to exercise the density table.
RECIPES = [
    ("Chocolate Truffle Cake", 1, "kg", 5, "Moist chocolate sponge layered and coated with dark ganache.", [
        ("Maida", 1.25, "cup"), ("Cocoa Powder", 0.5, "cup"), ("Sugar", 1, "cup"),
        ("Baking Powder", 1, "tsp"), ("Baking Soda", 0.5, "tsp"), ("Salt", 0.25, "tsp"),
        ("Eggs", 2, "pcs"), ("Milk", 1, "cup"), ("Vegetable Oil", 0.5, "cup"),
        ("Vanilla Essence", 1, "tsp"), ("Instant Coffee", 1, "tsp"),
        ("Dark Compound Chocolate", 300, "g"), ("Fresh Cream", 200, "ml"), ("Butter", 30, "g"),
    ]),
    ("Vanilla Sponge Cake", 500, "g", 5, "Classic butter sponge; base for fruit and cream cakes.", [
        ("Maida", 1, "cup"), ("Sugar", 0.75, "cup"), ("Butter", 100, "g"), ("Eggs", 2, "pcs"),
        ("Milk", 0.5, "cup"), ("Baking Powder", 1.5, "tsp"), ("Vanilla Essence", 1, "tsp"),
        ("Salt", 0.25, "tsp"),
    ]),
    ("Walnut Brownies", 12, "pcs", 8, "Fudgy brownies, 9x9 inch tin cut into 12.", [
        ("Dark Compound Chocolate", 200, "g"), ("Butter", 150, "g"), ("Sugar", 200, "g"),
        ("Eggs", 3, "pcs"), ("Maida", 100, "g"), ("Cocoa Powder", 30, "g"), ("Walnuts", 60, "g"),
        ("Salt", 0.25, "tsp"), ("Vanilla Essence", 1, "tsp"),
    ]),
    ("Choco Chip Cookies", 24, "pcs", 5, "Chewy cookies, about 25 g dough each.", [
        ("Butter", 115, "g"), ("Brown Sugar", 100, "g"), ("Sugar", 50, "g"), ("Eggs", 1, "pcs"),
        ("Maida", 1.75, "cup"), ("Baking Soda", 0.5, "tsp"), ("Salt", 0.5, "tsp"),
        ("Chocolate Chips", 150, "g"), ("Vanilla Essence", 1, "tsp"),
    ]),
]

FIXED_COSTS = [
    {"name": "Gas", "type": "per_kg", "value": 25, "active": True},
    {"name": "Electricity (oven)", "type": "per_kg", "value": 20, "active": True},
    {"name": "Labour", "type": "per_kg", "value": 100, "active": True},
    {"name": "Packaging (box + board)", "type": "per_order", "value": 40, "active": True},
    {"name": "Delivery", "type": "per_order", "value": 50, "active": True},
    {"name": "Misc. overheads", "type": "percent_of_cost", "value": 5, "active": True},
]

DEMO_SETTINGS = {
    "business_name": "Sweet Crumbs Home Bakery",
    "address": "12, Rose Lane, Indiranagar, Bengaluru 560038",
    "phone": "+91 90000 00000",
    "gstin": "",
    "tax_label": "GST",
    "tax_percent": 5.0,        # an example only - set your own rate in Settings
    "rounding_step": 10,
    "default_profit_mode": "margin",
    "default_profit_percent": 50.0,
}


def load_demo_data(conn, today: date | None = None) -> dict:
    """Replace all ingredients, recipes, fixed costs and invoices with demo data.

    Returns counts so the UI can confirm what was loaded.
    """
    today = today or date.today()
    db.clear_all(conn)

    ids: dict[str, int] = {}
    for name, category, base_unit, display_price, aliases in INGREDIENTS:
        price = price_from_display(display_price, base_unit)
        age = STALE.get(name, 0)
        # Give each item an older price too, so "price history" has something to show.
        older = round(price * 0.94, 6)
        ids[name] = db.add_ingredient(
            conn, name, base_unit, older, category, aliases,
            source="manual", on_date=(today - timedelta(days=age + 45)).isoformat(),
        )
        db.update_price(conn, ids[name], price, source="manual", on_date=(today - timedelta(days=age)).isoformat())

    for name, yq, yu, wastage, notes, rows in RECIPES:
        items = [{"ingredient_id": ids[ing], "quantity": q, "unit": u} for ing, q, u in rows]
        db.save_recipe(conn, name, yq, yu, items, wastage, notes)

    db.replace_fixed_costs(conn, FIXED_COSTS)
    db.save_settings(conn, DEMO_SETTINGS)
    return db.counts(conn)


if __name__ == "__main__":
    if "--yes" not in sys.argv:
        answer = input("This replaces ALL ingredients, recipes, fixed costs and invoices with demo data. Continue? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled - nothing changed.")
            sys.exit(0)
    result = load_demo_data(db.connect())
    print(f"Loaded demo data: {result}")
