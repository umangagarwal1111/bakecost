"""Recipe Book: create and edit recipes, set their yield, see what a batch costs."""

import pandas as pd
import streamlit as st

from modules import costing, db, units
from views.common import (
    ALL_RECIPE_UNITS, YIELD_UNITS, empty_library_hint, flash, friendly_errors, get_conn, inr, show_flash,
)

conn = get_conn()
st.title("📖 Recipe Book")
st.caption("A recipe lists what goes into ONE batch and how much that batch makes (its yield). "
           "Orders are priced by scaling the batch up or down.")
show_flash()
if empty_library_hint(conn):
    st.stop()

NEW = 0
recipes = {r["id"]: r for r in db.list_recipes(conn)}
# A page can't change a widget's value after drawing it, so a save stores the
# recipe to select next in "_recipe_next" and we apply it here, before drawing.
if "_recipe_next" in st.session_state:
    st.session_state["recipe_pick"] = st.session_state.pop("_recipe_next")
picked = st.selectbox("Recipe", [NEW, *recipes], key="recipe_pick",
                      format_func=lambda i: "➕ New recipe" if i == NEW else
                      f"{recipes[i]['name']}  ({recipes[i]['yield_quantity']:g} {recipes[i]['yield_unit']})")

recipe = db.get_recipe(conn, picked) if picked != NEW else {
    "name": "", "yield_quantity": 1.0, "yield_unit": "kg", "wastage_percent": 5.0, "notes": "", "items": []}

ingredients = {i["name"]: i for i in db.list_ingredients(conn)}

c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
name = c1.text_input("Recipe name *", recipe["name"], key=f"name_{picked}")
yield_qty = c2.number_input("Makes (yield) *", min_value=0.01, value=float(recipe["yield_quantity"]),
                            step=1.0, key=f"yq_{picked}")
yield_unit = c3.selectbox("Yield unit", YIELD_UNITS, index=YIELD_UNITS.index(units.normalize_unit(recipe["yield_unit"]))
                          if units.normalize_unit(recipe["yield_unit"]) in YIELD_UNITS else 0, key=f"yu_{picked}")
wastage = c4.number_input("Wastage %", min_value=0.0, max_value=50.0, value=float(recipe["wastage_percent"]),
                          step=1.0, key=f"w_{picked}",
                          help="Batter left in the bowl, broken pieces, trimmings. 5 % is typical.")
notes = st.text_area("Notes", recipe["notes"], height=68, key=f"notes_{picked}")

st.markdown("**Ingredients for one batch** — add rows at the bottom; select a row and press Delete to remove it.")
start = pd.DataFrame(
    [{"Ingredient": it["ingredient_name"], "Quantity": it["quantity"], "Unit": units.normalize_unit(it["unit"])}
     for it in recipe["items"]],
    columns=["Ingredient", "Quantity", "Unit"],
)
edited = st.data_editor(
    start, num_rows="dynamic", width="stretch", key=f"items_{picked}",
    column_config={
        "Ingredient": st.column_config.SelectboxColumn(options=sorted(ingredients), required=True, width="medium"),
        "Quantity": st.column_config.NumberColumn(min_value=0.0, step=0.25, required=True),
        "Unit": st.column_config.SelectboxColumn(options=ALL_RECIPE_UNITS, required=True, default="g"),
    },
)

# ---- validate rows + live cost preview ----------------------------------------
problems, items, lines = [], [], []
for n, row in enumerate(edited.itertuples(index=False), start=1):
    ing_name, qty, unit = row.Ingredient, row.Quantity, row.Unit
    if pd.isna(ing_name) and (pd.isna(qty) or not qty):
        continue  # a blank row the user just added
    if pd.isna(ing_name) or ing_name not in ingredients:
        problems.append(f"Row {n}: choose an ingredient.")
        continue
    if pd.isna(qty) or qty <= 0:
        problems.append(f"Row {n} ({ing_name}): quantity must be more than 0.")
        continue
    ing = ingredients[ing_name]
    try:
        units.to_base(qty, unit, ing["base_unit"], ing_name)
    except units.UnitConversionError as exc:
        problems.append(f"Row {n}: {exc}")
        continue
    items.append({"ingredient_id": ing["id"], "quantity": float(qty), "unit": unit})
    lines.append(costing.IngredientLine(ing_name, float(qty), unit, ing["base_unit"],
                                        ing["current_price_per_base_unit"], ing["last_updated"]))

if lines:
    est = costing.estimate_cost(lines, 1.0, wastage)
    m1, m2, m3 = st.columns(3)
    m1.metric("Ingredient cost per batch", inr(est.total))
    # Show cost per kg / per piece / per litre - the way bakers quote.
    per_unit = {"mass": "kg", "count": "pcs", "volume": "l"}[units.dimension_of(yield_unit)]
    m2.metric(f"Ingredient cost per {'piece' if per_unit == 'pcs' else per_unit}",
              inr(est.total / units.convert(yield_qty, yield_unit, per_unit)))
    m3.metric("Includes wastage", inr(est.wastage_amount))
    for w in est.warnings:
        st.warning(w, icon="⚠️")
for p in problems:
    st.error(p)

b1, b2, _ = st.columns([2, 2, 3])
if b1.button("💾 Save recipe", type="primary", disabled=bool(problems)):
    with friendly_errors("Couldn't save the recipe"):
        new_id = db.save_recipe(conn, name, yield_qty, yield_unit, items, wastage, notes,
                                recipe_id=None if picked == NEW else picked)
        st.session_state["_recipe_next"] = new_id
        flash(f"Saved **{name.strip()}**.")
        st.rerun()
if picked != NEW and b2.button("🗑️ Delete"):
    st.session_state["confirm_delete"] = picked
if st.session_state.get("confirm_delete") == picked:
    st.warning(f"Delete **{recipe['name']}** permanently? Past invoices are not affected.")
    y, n, _ = st.columns([2, 2, 3])
    if y.button("Yes, delete", type="primary"):
        db.delete_recipe(conn, picked)
        st.session_state.pop("confirm_delete")
        st.session_state["_recipe_next"] = NEW
        flash(f"Deleted **{recipe['name']}**.")
        st.rerun()
    if n.button("Cancel"):
        st.session_state.pop("confirm_delete")
        st.rerun()
