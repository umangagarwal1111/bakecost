"""Ingredient Library: view, search, add, edit, update prices, see price history."""

from datetime import date

import pandas as pd
import streamlit as st

from modules import db, units
from modules.costing import STALE_PRICE_DAYS
from views.common import BASE_UNIT_LABELS, UNITS_FOR_BASE, flash, friendly_errors, get_conn, price_label, show_flash

conn = get_conn()
st.title("🥚 Ingredient Library")
st.caption("Every ingredient you bake with, and what it costs you. Prices are shown per kg, per litre or per piece.")
show_flash()

tab_list, tab_add, tab_edit = st.tabs(["📋 All ingredients", "➕ Add ingredient", "✏️ Edit / price history"])


def _age_days(iso: str | None) -> int | None:
    return None if not iso else (date.today() - date.fromisoformat(iso[:10])).days


# --------------------------------------------------------------------------- list
with tab_list:
    search = st.text_input("🔍 Search", placeholder="Type a name, alias (e.g. 'maida') or category")
    rows = db.list_ingredients(conn, search)
    if not rows:
        st.info("No ingredients found." if search else
                "No ingredients yet - add one in the next tab, or load demo data from the Dashboard.")
    else:
        table = []
        for r in rows:
            age = _age_days(r["last_updated"])
            if r["current_price_per_base_unit"] is None:
                status = "⚠️ No price yet - add one"
            elif age is not None and age > STALE_PRICE_DAYS:
                status = f"⏰ Old price ({age} days) - check it"
            else:
                status = "✅ Up to date"
            table.append({
                "Name": r["name"], "Category": r["category"],
                "Price": price_label(r["current_price_per_base_unit"], r["base_unit"]),
                "Price check": status,
                "Last updated": r["last_updated"] or "—",
                "Also known as": r["aliases"],
            })
        st.dataframe(
            pd.DataFrame(table), hide_index=True, width="stretch",
            column_config={
                "Price check": st.column_config.TextColumn(
                    width="medium",
                    help=f"✅ Up to date: price updated in the last {STALE_PRICE_DAYS} days.  "
                         f"⏰ Old price: more than {STALE_PRICE_DAYS} days old, so shop prices may have changed. "
                         "⚠️ No price yet: orders using it will be under-costed.",
                ),
                "Also known as": st.column_config.TextColumn(help="Other names on shop bills, used by Scan a Bill."),
            },
        )
        stale = sum(1 for t in table if not t["Price check"].startswith("✅"))
        st.caption(
            f"{len(rows)} ingredient(s). **Price check** tells you whether a price is safe to quote with: "
            f"✅ up to date · ⏰ older than {STALE_PRICE_DAYS} days · ⚠️ no price yet."
            + (f" **{stale} need attention** - update them in the *Edit / price history* tab or by scanning a bill."
               if stale else "")
        )

# --------------------------------------------------------------------------- add
with tab_add:
    st.subheader("Add a new ingredient")
    base_unit = st.radio("How do you measure it?", list(BASE_UNIT_LABELS), format_func=BASE_UNIT_LABELS.get,
                         horizontal=True, key="add_base",
                         help="This can't be changed later, because every recipe and price depends on it.")
    with st.form("add_ingredient", clear_on_submit=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("Name *", placeholder="e.g. Maida")
        existing = db.list_categories(conn)
        category = c2.text_input("Category", placeholder="e.g. Flours & Starches",
                                 help="Existing: " + (", ".join(existing) if existing else "none yet"))
        aliases = st.text_input("Other names (comma separated)", placeholder="all purpose flour, apf, refined flour",
                                help="Bill scanning uses these to recognise the item on shop bills.")
        st.markdown("**What did you pay?** *(leave the price at 0 if you don't know it yet)*")
        p1, p2, p3 = st.columns([2, 2, 2])
        amount = p1.number_input("I paid (₹)", min_value=0.0, step=1.0, format="%.2f")
        qty = p2.number_input("for this much", min_value=0.0, value=1.0, step=1.0)
        unit = p3.selectbox("unit", [u for u in UNITS_FOR_BASE[base_unit] if u not in ("cup", "tbsp", "tsp")])
        if st.form_submit_button("Add ingredient", type="primary"):
            with friendly_errors("Couldn't add the ingredient"):
                price = None
                base_qty = None
                if amount > 0:
                    if qty <= 0:
                        raise ValueError("Enter how much you bought for that price.")
                    base_qty = units.to_base(qty, unit, base_unit, name)
                    price = amount / base_qty
                db.add_ingredient(conn, name, base_unit, price, category or "Other", aliases, base_qty)
                flash(f"Added **{name.strip()}**" + (f" at {price_label(price, base_unit)}." if price else
                                                     " (no price yet)."))
                st.rerun()

# --------------------------------------------------------------------------- edit
with tab_edit:
    rows = db.list_ingredients(conn)
    if not rows:
        st.info("Nothing to edit yet.")
        st.stop()
    by_id = {r["id"]: r for r in rows}
    chosen = st.selectbox("Choose an ingredient", list(by_id), format_func=lambda i: by_id[i]["name"])
    ing = by_id[chosen]
    st.markdown(f"Measured in **{BASE_UNIT_LABELS[ing['base_unit']]}** · current price "
                f"**{price_label(ing['current_price_per_base_unit'], ing['base_unit'])}** "
                f"· last updated {ing['last_updated'] or 'never'}")

    left, right = st.columns(2)
    with left:
        with st.form(f"edit_{chosen}"):
            st.markdown("**Details**")
            new_name = st.text_input("Name", ing["name"])
            new_cat = st.text_input("Category", ing["category"])
            new_aliases = st.text_area("Other names (comma separated)", ing["aliases"], height=80)
            if st.form_submit_button("Save details"):
                with friendly_errors("Couldn't save"):
                    db.update_ingredient(conn, chosen, new_name, new_aliases, new_cat)
                    flash(f"Saved details for **{new_name.strip()}**.")
                    st.rerun()
    with right:
        with st.form(f"price_{chosen}"):
            st.markdown("**Update price**")
            q1, q2, q3 = st.columns(3)
            paid = q1.number_input("I paid (₹)", min_value=0.0, step=1.0, format="%.2f")
            bought = q2.number_input("for", min_value=0.0, value=1.0, step=1.0)
            bunit = q3.selectbox("unit", [u for u in UNITS_FOR_BASE[ing["base_unit"]] if u not in ("cup", "tbsp", "tsp")])
            on = st.date_input("Date bought", date.today(), max_value=date.today())
            if st.form_submit_button("Save new price", type="primary"):
                with friendly_errors("Couldn't update the price"):
                    if paid <= 0 or bought <= 0:
                        raise ValueError("Enter both the amount paid and how much you bought.")
                    base_qty = units.to_base(bought, bunit, ing["base_unit"], ing["name"])
                    db.update_price(conn, chosen, paid / base_qty, base_qty, "manual", on.isoformat())
                    flash(f"New price for **{ing['name']}**: {price_label(paid / base_qty, ing['base_unit'])}")
                    st.rerun()

    st.markdown("**Price history**")
    history = db.price_history(conn, chosen)
    if history:
        per = units.DISPLAY_UNIT[ing["base_unit"]][0]
        hist = pd.DataFrame([{
            "Date": h["date"],
            f"Price (₹/{per})": units.price_for_display(h["price_per_base_unit"], ing["base_unit"])[0],
            "Source": "🧾 bill" if h["source"] == "bill" else "✍️ manual",
            "Bought": "—" if h["quantity_bought"] is None else f"{h['quantity_bought']:g} {ing['base_unit']}",
        } for h in history])
        if len(hist) > 1:
            st.line_chart(hist.sort_values("Date"), x="Date", y=f"Price (₹/{per})", height=200)
        st.dataframe(hist, hide_index=True, width="stretch",
                     column_config={f"Price (₹/{per})": st.column_config.NumberColumn(format="₹%.2f")})
    else:
        st.caption("No price recorded yet.")

    with st.expander("🗑️ Delete this ingredient"):
        st.warning("This removes the ingredient and its price history. Ingredients used in a recipe can't be deleted.")
        if st.button(f"Delete {ing['name']}", type="secondary"):
            with friendly_errors("Couldn't delete"):
                db.delete_ingredient(conn, chosen)
                flash(f"Deleted **{ing['name']}**.")
                st.rerun()
