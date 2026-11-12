"""Scan a recipe -> review ingredient lines -> save as a recipe in the Recipe Book."""

import hashlib

import pandas as pd
import streamlit as st

from modules import db, units
from modules.matching import AUTO_SELECT_THRESHOLD, rank_matches
from modules.parsing import parse_recipe
from views.common import ALL_RECIPE_UNITS, YIELD_UNITS, candidates, flash, friendly_errors, get_conn, show_flash
from views.scan_common import ocr_step, pick_image

NEW = "➕ add as new ingredient"
SKIP = "— leave out —"


def guess_base_unit(ingredient_name: str, unit: str | None) -> str:
    """Pre-fill how a NEW ingredient is measured. Powders and granules measured
    in cups (flour, sugar: density below 0.9 g/ml) are still bought by weight,
    so suggest grams; otherwise follow the recipe unit. The user can change it."""
    if not unit or not units.is_known_unit(unit):
        return "g"
    density = units.density_for(ingredient_name)
    if units.dimension_of(unit) == units.VOLUME and density is not None and density < 0.9:
        return "g"
    return units.base_unit_for(unit)


conn = get_conn()
st.title("📝 Scan a Recipe")
st.caption("Photograph a recipe card or book page. BakeCost lists the ingredients it finds; "
           "you check them and save it to your Recipe Book.")
st.info("**Printed recipes work best.** Handwritten recipes are much harder for the reader — expect to "
        "correct quantities and names by hand, or simply type the recipe into the box below.", icon="✍️")
show_flash()

st.subheader("1 · Photo")
image_bytes = pick_image("recipe", "recipe_*.png")
ocr_step(image_bytes, "recipe_text")

st.subheader("2 · Recipe text")
text = st.text_area("Fix any reading mistakes — or type/paste the recipe, one ingredient per line",
                    key="recipe_text", height=240,
                    placeholder="Eggless Vanilla Cake\nMakes 500 g\n1 1/2 cups maida\n3/4 cup sugar\n1 cup curd")
if not text.strip():
    st.stop()

parsed = parse_recipe(text)
cands = candidates(conn)
library = {i["name"]: i for i in db.list_ingredients(conn)}
key = hashlib.md5(text.encode()).hexdigest()[:10]   # new text => fresh form

st.subheader("3 · Check and save")
c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
name = c1.text_input("Recipe name *", parsed.title or "", key=f"rs_name_{key}")
yq = c2.number_input("Makes (yield) *", min_value=0.01, value=float(parsed.yield_quantity or 1), key=f"rs_yq_{key}")
default_unit = parsed.yield_unit if parsed.yield_unit in YIELD_UNITS else "kg"
yu = c3.selectbox("Yield unit", YIELD_UNITS, index=YIELD_UNITS.index(default_unit), key=f"rs_yu_{key}")
wastage = c4.number_input("Wastage %", min_value=0.0, max_value=50.0, value=5.0, key=f"rs_w_{key}")
if parsed.yield_quantity is None:
    st.warning("Couldn't find how much the recipe makes (e.g. 'Makes 12' or 'Yield: 1 kg') — please set the yield.")

rows = []
for line in parsed.lines:
    ranked = rank_matches(line.name, cands, top_n=1)
    best = ranked[0] if ranked else None
    confident = best is not None and best.score >= AUTO_SELECT_THRESHOLD
    rows.append({
        "From the photo": line.raw,
        "Ingredient": best.ingredient_name if confident else NEW,
        "Suggestion": f"{best.ingredient_name} ({best.score:.0%})" if best and not confident else "",
        "Name if new": line.name.title(),
        "Measure new in": guess_base_unit(line.name, line.unit),
        "Qty": line.quantity,
        "Unit": line.unit,
    })
st.caption(f"Lines matched with {AUTO_SELECT_THRESHOLD:.0%}+ confidence are linked to your library. "
           "Others become new ingredients unless you pick one — see the Suggestion column.")
edited = st.data_editor(
    pd.DataFrame(rows, columns=["From the photo", "Ingredient", "Suggestion", "Name if new", "Measure new in", "Qty", "Unit"]),
    key=f"rs_items_{key}", num_rows="dynamic", hide_index=True, width="stretch",
    disabled=["From the photo", "Suggestion"],
    column_config={
        "Ingredient": st.column_config.SelectboxColumn(options=[NEW, SKIP, *sorted(library)], required=True, default=NEW),
        "Measure new in": st.column_config.SelectboxColumn(
            options=list(units.BASE_UNITS), help="How a NEW ingredient is bought: g (weight), ml (volume) or pcs"),
        "Qty": st.column_config.NumberColumn(min_value=0.0, step=0.25),
        "Unit": st.column_config.SelectboxColumn(options=ALL_RECIPE_UNITS),
    },
)


problems, plan = [], []
for n, row in enumerate(edited.to_dict("records"), start=1):
    choice, qty, unit = row.get("Ingredient"), row.get("Qty"), row.get("Unit")
    if choice == SKIP:
        continue
    label = row.get("From the photo") or f"row {n}"
    if qty is None or pd.isna(qty) or qty <= 0:
        problems.append(f"**{label}**: enter a quantity.")
        continue
    if not unit or pd.isna(unit):
        problems.append(f"**{label}**: choose a unit.")
        continue
    if choice == NEW:
        new_name = str(row.get("Name if new") or "").strip()
        if not new_name:
            problems.append(f"**{label}**: give the new ingredient a name.")
            continue
        existing = db.find_ingredient_by_name(conn, new_name)
        if existing:            # typed a name that already exists: just use it
            choice = existing["name"]
        else:
            base = row.get("Measure new in") or guess_base_unit(new_name, unit)
            try:
                units.to_base(qty, unit, base, new_name)
                plan.append(("new", new_name, float(qty), unit, base))
            except units.UnitConversionError as exc:
                problems.append(f"**{label}**: {exc}")
            continue
    ing = library[choice]
    try:
        units.to_base(qty, unit, ing["base_unit"], ing["name"])
        plan.append(("existing", ing["name"], float(qty), unit, ing["base_unit"]))
    except units.UnitConversionError as exc:
        problems.append(f"**{label}**: {exc}")

for p in problems:
    st.error(p)
new_count = sum(1 for p in plan if p[0] == "new")
if new_count:
    st.info(f"{new_count} new ingredient(s) will be added **without a price**. "
            "Add their prices on the Ingredients page (or scan a bill) before quoting.")

if st.button("💾 Save to Recipe Book", type="primary", disabled=bool(problems) or not plan):
    with friendly_errors("Couldn't save the recipe"):
        if not name.strip():
            raise ValueError("Give the recipe a name.")
        items = []
        for kind, ing_name, qty, unit, base in plan:
            ing = db.find_ingredient_by_name(conn, ing_name)
            ing_id = ing["id"] if ing else db.add_ingredient(conn, ing_name, base, None, "Added from recipe scan")
            items.append({"ingredient_id": ing_id, "quantity": qty, "unit": unit})
        recipe_id = db.save_recipe(conn, name, yq, yu, items, wastage, "Created from a scanned recipe.")
        flash(f"Saved **{name.strip()}** with {len(items)} ingredients.")
        st.session_state["_recipe_next"] = recipe_id
        st.switch_page("views/recipes.py")
