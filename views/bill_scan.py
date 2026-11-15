"""Scan a shop bill -> review every line -> update ingredient prices.

Nothing read from a photo is saved until the user has checked it and pressed
the save button. Rows the matcher isn't confident about start un-ticked.
"""

import hashlib
from datetime import date

import pandas as pd
import streamlit as st

from modules import db, units
from modules.matching import AUTO_SELECT_THRESHOLD, best_match, should_preselect
from modules.parsing import format_bill_text, parse_bill
from views.common import ALL_RECIPE_UNITS, candidates, empty_library_hint, flash, get_conn, inr, show_flash
from views.scan_common import ocr_step, pick_image, use_fixed_width_text_boxes

SKIP = "— don't save —"
BIG_CHANGE = 0.5  # warn when a price moves by more than 50 %

conn = get_conn()
st.title("📷 Scan a Bill")
st.caption("Photograph a **printed or digital** shop bill. BakeCost reads it, **you check every line**, "
           "and only the lines you confirm update your prices. Handwritten bills can't be read reliably.")
show_flash()
use_fixed_width_text_boxes()
if empty_library_hint(conn):
    st.stop()

# ---- Step 1: photo -> text ----------------------------------------------------------
st.subheader("1 · Photo")
image_bytes = pick_image("bill", "bill_*.png")
if "_bill_text_next" in st.session_state:          # set by a previous save; see ocr_step()
    st.session_state["bill_text"] = st.session_state.pop("_bill_text_next")
ocr_step(image_bytes, "bill_text", reject_handwriting=True, tidy=format_bill_text)

st.subheader("2 · Bill text")
# Grow the box with the bill (about 26 px per line) so nothing hides below the fold.
line_count = st.session_state.get("bill_text", "").count("\n") + 1
text = st.text_area(
    "Fix any reading mistakes here — or type/paste the bill yourself, one item per line",
    key="bill_text", height=max(220, min(560, 40 + 26 * line_count)),
    placeholder=format_bill_text("Item Qty Rate Amount\nMaida 1kg 2 45.00 90.00\n"
                                 "Amul Butter 500g 1 280.00 280.00\nEggs 30 7.00 210.00"),
)
if not text.strip():
    st.stop()

# ---- Step 2: text -> suggested rows -------------------------------------------------
cands = candidates(conn)
library = {i["name"]: i for i in db.list_ingredients(conn)}
parsed = parse_bill(text)

rows = []
for item in parsed.items:
    match = best_match(item.name, cands)
    rows.append({
        "Save": should_preselect(match) and item.quantity is not None,
        "On the bill": item.name,
        "Ingredient": match.ingredient_name if match else SKIP,
        "Match %": round(match.score * 100) if match else 0,
        "Qty": item.quantity,
        "Unit": units.normalize_unit(item.unit) if item.unit else None,
        "Amount ₹": item.amount,
    })
if not rows:
    st.warning("No item lines found. Each line needs a name and an amount, e.g. `Sugar 1kg 48.00`.")
if parsed.skipped:
    with st.expander(f"Lines ignored ({len(parsed.skipped)}) — totals, taxes, headers"):
        st.code("\n".join(parsed.skipped), language=None)

st.subheader("3 · Check and confirm")
st.caption(f"✅ = will be saved. Rows matched with less than {AUTO_SELECT_THRESHOLD:.0%} confidence start un-ticked. "
           "Change the ingredient, quantity or unit if anything is wrong; add a row for anything that was missed.")
editor_key = "bill_review_" + hashlib.md5(text.encode()).hexdigest()[:10]   # new text => fresh table
edited = st.data_editor(
    pd.DataFrame(rows, columns=["Save", "On the bill", "Ingredient", "Match %", "Qty", "Unit", "Amount ₹"]),
    key=editor_key, num_rows="dynamic", hide_index=True, width="stretch",
    column_config={
        "Save": st.column_config.CheckboxColumn(default=False, width="small"),
        "On the bill": st.column_config.TextColumn(disabled=True),
        "Ingredient": st.column_config.SelectboxColumn(options=[SKIP, *sorted(library)], required=True, default=SKIP),
        "Match %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%", width="small"),
        "Qty": st.column_config.NumberColumn(min_value=0.0, step=0.5, help="Total quantity bought on this line"),
        "Unit": st.column_config.SelectboxColumn(options=ALL_RECIPE_UNITS),
        "Amount ₹": st.column_config.NumberColumn(min_value=0.0, format="₹%.2f"),
    },
    disabled=["On the bill", "Match %"],
)

# ---- Step 3: validate and preview the effect of each row ------------------------------
ready, preview = [], []
seen: dict[str, int] = {}
for row in edited.to_dict("records"):
    name = row.get("Ingredient")
    qty, unit, amount = row.get("Qty"), row.get("Unit"), row.get("Amount ₹")
    status, new_price, change = "", None, None
    ing = library.get(name)
    if not row.get("Save"):
        status = "not saving"
    elif ing is None:
        status = "❌ choose an ingredient"
    elif qty is None or pd.isna(qty) or qty <= 0:
        status = "❌ enter the quantity bought"
    elif not unit or pd.isna(unit):
        status = "❌ choose a unit"
    elif amount is None or pd.isna(amount) or amount <= 0:
        status = "❌ enter the amount paid"
    else:
        try:
            base_qty = units.to_base(qty, unit, ing["base_unit"], ing["name"])
            new_price = amount / base_qty
            old = ing["current_price_per_base_unit"]
            change = None if not old else (new_price - old) / old
            status = "✅ ready"
            if change is not None and abs(change) > BIG_CHANGE:
                status = f"⚠️ big change ({change:+.0%}) — double-check qty/unit"
            seen[name] = seen.get(name, 0) + 1
            ready.append((ing, new_price, base_qty, row.get("On the bill")))
        except units.UnitConversionError as exc:
            status = f"❌ {exc}"
    if ing is not None or row.get("Save"):
        per = units.DISPLAY_UNIT[ing["base_unit"]][0] if ing else ""
        preview.append({
            "Ingredient": name if ing else "—",
            "New price": "" if new_price is None else f"{inr(units.price_for_display(new_price, ing['base_unit'])[0])}/{per}",
            "Current price": "" if not ing or ing["current_price_per_base_unit"] is None else
                f"{inr(units.price_for_display(ing['current_price_per_base_unit'], ing['base_unit'])[0])}/{per}",
            "Change": "" if change is None else f"{change:+.1%}",
            "Status": status,
        })

if preview:
    st.dataframe(pd.DataFrame(preview), hide_index=True, width="stretch")
for name, n in seen.items():
    if n > 1:
        st.info(f"**{name}** appears {n} times; the last line will become the current price.")

c1, c2 = st.columns(2)
bill_date = c1.date_input("Date on the bill", date.today(), max_value=date.today())
remember = c2.checkbox("Remember these bill names for next time", value=True,
                       help="Adds the name printed on the bill (e.g. 'Amul Butter') to the ingredient's "
                            "other names, so it is matched with full confidence next time.")

if st.button(f"✅ Update {len(ready)} price(s)", type="primary", disabled=not ready):
    for ing, price, base_qty, bill_name in ready:
        db.update_price(conn, ing["id"], price, base_qty, "bill", bill_date.isoformat())
        fresh = db.get_ingredient(conn, ing["id"])  # re-read: an earlier row may have added an alias
        known = {fresh["name"].lower(), *(a.lower() for a in db.split_aliases(fresh["aliases"]))}
        if remember and bill_name and bill_name.strip().lower() not in known:
            aliases = ", ".join([*db.split_aliases(fresh["aliases"]), bill_name.strip()])
            db.update_ingredient(conn, fresh["id"], fresh["name"], aliases, fresh["category"])
    flash("Updated prices: " + ", ".join(
        f"**{ing['name']}** → {inr(units.price_for_display(p, ing['base_unit'])[0])}/{units.DISPLAY_UNIT[ing['base_unit']][0]}"
        for ing, p, _, _ in ready))
    st.session_state["_bill_text_next"] = ""
    st.rerun()
