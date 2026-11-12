"""Dashboard: what needs attention, recent invoices, priciest ingredients, demo data."""

from datetime import date

import pandas as pd
import streamlit as st

import seed
from modules import db, invoice
from modules.costing import STALE_PRICE_DAYS
from views.common import demo_mode, flash, get_conn, inr, price_label, show_flash

conn = get_conn()
settings = db.get_settings(conn)
st.title(f"🧁 {settings['business_name']}")
st.caption("BakeCost — know what every cake costs you, and price it with confidence.")
show_flash()

counts = db.counts(conn)
ingredients = db.list_ingredients(conn)
attention = [
    i for i in ingredients
    if i["current_price_per_base_unit"] is None
    or (i["last_updated"] and (date.today() - date.fromisoformat(i["last_updated"][:10])).days > STALE_PRICE_DAYS)
]

if counts["ingredients"] == 0:
    st.info("👋 **Welcome!** Start by loading the demo data below to explore the app, "
            "or add your own ingredients on the **Ingredients** page.")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Ingredients", counts["ingredients"])
m2.metric("Recipes", counts["recipes"])
m3.metric("Invoices", counts["invoices"])
m4.metric("Prices to check", len(attention),
          help=f"Ingredients with no price, or a price older than {STALE_PRICE_DAYS} days.")

left, right = st.columns([3, 2])

with left:
    st.subheader("Recent invoices")
    recent = db.list_invoices(conn, limit=10)
    if not recent:
        st.caption("No invoices yet — create one on the **New Order** page.")
    else:
        st.dataframe(
            pd.DataFrame([{"Invoice": r["number"], "Date": r["date"], "Customer": r["customer_name"],
                           "Total": inr(r["total"])} for r in recent]),
            hide_index=True, width="stretch",
        )
        by_id = {r["id"]: r for r in recent}
        pick = st.selectbox("Download again", list(by_id),
                            format_func=lambda i: f"{by_id[i]['number']} – {by_id[i]['customer_name']}")
        inv = db.get_invoice(conn, pick)
        d1, d2 = st.columns(2)
        d1.download_button("📄 Invoice PDF", invoice.build_invoice_pdf(inv, settings),
                           file_name=f"{inv['number']}.pdf", mime="application/pdf", width="stretch")
        d2.download_button("🔒 Cost sheet PDF", invoice.build_cost_sheet_pdf(inv, settings),
                           file_name=f"{inv['number']}-cost-sheet.pdf", mime="application/pdf", width="stretch")

with right:
    st.subheader("Most expensive ingredients")
    top = db.most_expensive_ingredients(conn, limit=6)
    if top:
        st.dataframe(
            pd.DataFrame([{"Ingredient": t["name"],
                           "Price": price_label(t["current_price_per_base_unit"], t["base_unit"])} for t in top]),
            hide_index=True, width="stretch",
        )
        st.caption("Per kg for weighed items, per litre for liquids, per piece for counted items.")
    else:
        st.caption("No prices yet.")

    if attention:
        st.subheader("Prices to check")
        for i in attention[:8]:
            why = "no price" if i["current_price_per_base_unit"] is None else f"last updated {i['last_updated']}"
            st.markdown(f"- **{i['name']}** — {why}")
        st.caption("Update them on **Ingredients** or by scanning a bill.")

st.divider()
with st.expander("🎬 Demo data", expanded=counts["ingredients"] == 0):
    st.markdown(
        "Loads **25 common ingredients** at typical Indian prices, **4 recipes** (chocolate truffle cake 1 kg, "
        "vanilla sponge 500 g, brownies 12 pcs, cookies 24 pcs), sample fixed costs and business details."
    )
    # In the hosted demo the data is private and disposable, so no warning is needed.
    has_data = not demo_mode() and any(counts[k] for k in ("ingredients", "recipes", "invoices"))
    confirmed = True
    if has_data:
        st.warning("This **replaces** all your ingredients, recipes, fixed costs and invoices.")
        confirmed = st.checkbox("I understand — replace my data")
    if st.button("Load demo data", type="primary", disabled=not confirmed):
        loaded = seed.load_demo_data(conn)
        flash(f"Demo data loaded: {loaded['ingredients']} ingredients, {loaded['recipes']} recipes. "
              "Try **New Order** → Chocolate Truffle Cake.")
        st.rerun()
