"""New Order: pick a recipe and size -> cost breakdown -> profit & tax -> PDF invoice."""

import re

import pandas as pd
import streamlit as st

from modules import costing, db, invoice, units
from views.common import friendly_errors, get_conn, inr, price_label

conn = get_conn()
settings = db.get_settings(conn)
st.title("🧾 New Order")

recipes = {r["id"]: r for r in db.list_recipes(conn)}
if not recipes:
    st.info("You need at least one recipe. Create one in the **Recipe Book**, scan one, "
            "or load the demo data from the Dashboard.", icon="📖")
    st.stop()

ORDER_UNITS = {units.MASS: ["kg", "g"], units.COUNT: ["pcs", "dozen"], units.VOLUME: ["l", "ml"]}
PHONE_RE = re.compile(r"^(\+?91)?[6-9]\d{9}$")   # Indian mobile, optional +91


def money_table(rows: list[tuple[str, float]]) -> str:
    """Two-column markdown table: label | rupee amount (right-aligned)."""
    lines = ["| Item | ₹ |", "|---|---:|"] + [f"| {label} | {inr(value)} |" for label, value in rows]
    return "\n".join(lines)

# ---- 1. What and for whom --------------------------------------------------------------
st.subheader("1 · What is the customer ordering?")
c1, c2, c3 = st.columns([3, 1, 1])
rid = c1.selectbox("Recipe", list(recipes), format_func=lambda i: recipes[i]["name"])
recipe = db.get_recipe(conn, rid)
yield_unit = units.normalize_unit(recipe["yield_unit"])
unit_options = ORDER_UNITS[units.dimension_of(yield_unit)]
qty = c2.number_input("Quantity", min_value=0.01, value=float(recipe["yield_quantity"]) if yield_unit in unit_options
                      else 1.0, step=0.5 if unit_options[0] == "kg" else 1.0, key=f"qty_{rid}")
order_unit = c3.selectbox("Unit", unit_options, index=unit_options.index(yield_unit) if yield_unit in unit_options else 0,
                          key=f"unit_{rid}")
st.caption(f"One batch of this recipe makes **{recipe['yield_quantity']:g} {recipe['yield_unit']}**.")

d1, d2 = st.columns(2)
customer = d1.text_input("Customer name *", placeholder="e.g. Priya Sharma")
phone = d2.text_input("Customer phone", placeholder="10-digit mobile number")
phone_clean = re.sub(r"[\s\-()]", "", phone)
phone_ok = not phone_clean or bool(PHONE_RE.match(phone_clean))
if not phone_ok:
    d2.error("That doesn't look like a 10-digit Indian mobile number.")

# ---- 2. Pricing choices ----------------------------------------------------------------
st.subheader("2 · Profit and tax")
p1, p2, p3 = st.columns(3)
modes = {"margin": "Margin (profit as % of the selling price)", "markup": "Markup (profit as % of your cost)"}
mode = p1.radio("How do you want to add profit?", list(modes), format_func=modes.get,
                index=list(modes).index(settings["default_profit_mode"]))
profit = p2.number_input("Profit %", min_value=0.0, max_value=95.0 if mode == "margin" else 500.0,
                         value=min(float(settings["default_profit_percent"]), 95.0), step=5.0,
                         key=f"profit_{mode}")  # separate state per mode: the allowed range differs
tax = p3.number_input(f"{settings['tax_label']} %", min_value=0.0, max_value=100.0,
                      value=float(settings["tax_percent"]), step=0.5,
                      help="Default comes from Settings. Use 0 if you don't charge tax.")

fixed = db.list_fixed_costs(conn, active_only=True)
quote = None
with friendly_errors("Can't price this order"):
    quote = costing.build_quote(recipe, fixed, qty, order_unit, profit, mode, tax, settings["rounding_step"])
if quote is None:
    st.stop()
p = quote.price

with st.expander("🤔 Margin or markup? What's the difference?"):
    cost = p.production_cost
    st.markdown(
        f"Both add profit on top of your cost (**{inr(cost)}** for this order), but they measure "
        f"'{profit:g}%' against different things:\n\n"
        "- **Markup** = a % of *your cost*. Price = cost × (1 + markup).\n"
        "- **Margin** = a % of *the selling price*. Price = cost ÷ (1 − margin).\n\n"
        "So the same number gives a different price:"
    )
    rows = []
    for m in ("markup", "margin"):
        try:
            price = costing.apply_profit(cost, profit, m)
            rows.append({"Method": m.title(), "Selling price (before tax)": inr(price),
                         "Your profit": inr(price - cost),
                         "Profit as % of price": f"{(price - cost) / price:.1%}" if price else "—"})
        except ValueError as exc:
            rows.append({"Method": m.title(), "Selling price (before tax)": str(exc)})
    st.table(pd.DataFrame(rows).set_index("Method"))
    st.caption("Most bakers think in margin ('I keep half of what the customer pays'); "
               "markup is quicker to calculate in your head.")

# ---- 3. Cost breakdown -------------------------------------------------------------------
st.subheader("3 · Cost breakdown")
st.caption(f"Recipe made **×{quote.factor:g}** to fill {quote.order_text}. "
           f"Estimated weight {quote.weight_kg:.2f} kg (used for per-kg costs).")
st.dataframe(
    pd.DataFrame([{
        "Ingredient": l.name,
        "Needed": f"{l.quantity:g} {l.unit}" + ("" if l.base_quantity is None or l.unit == l.base_unit
                                                 else f"  (= {l.base_quantity:,.0f} {l.base_unit})"),
        "Price": price_label(l.price_per_base_unit, l.base_unit),
        "Cost": l.cost,
        "Check": "⚠️ " + "; ".join(l.flags) if l.flags else "✅",
    } for l in quote.estimate.lines]),
    hide_index=True, width="stretch",
    column_config={"Cost": st.column_config.NumberColumn(format="₹%.2f")},
)
for w in quote.estimate.warnings:
    st.warning(w, icon="⚠️")

left, right = st.columns(2)
with left:
    st.markdown("##### 🔒 Your cost sheet (internal)")
    sheet = [("Ingredients", quote.estimate.ingredients_subtotal),
             (f"Wastage ({quote.estimate.wastage_percent:g}%)", quote.estimate.wastage_amount)]
    sheet += [(f"+ {name}", amount) for name, amount in p.fixed_cost_lines]
    sheet += [("**Production cost**", p.production_cost),
              (f"Profit ({mode} {profit:g}%)", p.profit_amount),
              ("**Selling price**", p.selling_price)]
    st.markdown(money_table(sheet))
with right:
    st.markdown("##### 👀 What the customer sees")
    cust = [(f"{quote.recipe_name} — {quote.order_text}", p.selling_price)]
    if tax:
        cust.append((f"{settings['tax_label']} @ {tax:g}%", p.tax_amount))
    if abs(p.round_off) >= 0.005:
        cust.append(("Round off", p.round_off))
    cust.append(("**Total**", p.final_total))
    st.markdown(money_table(cust))

m1, m2, m3 = st.columns(3)
m1.metric("Your cost", inr(p.production_cost))
m2.metric("Your profit", inr(p.profit_amount + p.round_off),
          help="Includes the round-off, which goes to (or comes from) you.")
m3.metric("Customer pays", inr(p.final_total, 0))

# ---- 4. Invoice --------------------------------------------------------------------------
st.subheader("4 · Invoice")
missing = []
if not customer.strip():
    missing.append("customer name")
if not phone_ok:
    missing.append("a valid phone number (or leave it empty)")
if missing:
    st.caption("To create the invoice, enter " + " and ".join(missing) + ".")
# Fingerprint of everything on this order: the same order can't be invoiced
# twice by an accidental double click, but any change allows a new invoice.
signature = (rid, qty, order_unit, customer.strip(), phone_clean, mode, profit, tax, p.final_total)
already = st.session_state.get("last_invoice_sig") == signature
if st.button("🧾 Create invoice", type="primary", disabled=bool(missing) or already,
             help="Already created — change the order to make another." if already else None):
    with friendly_errors("Couldn't create the invoice"):
        inv = db.save_invoice(
            conn, customer, phone_clean,
            [{"description": quote.recipe_name, "quantity_text": quote.order_text, "amount": round(p.selling_price, 2)}],
            costing.cost_sheet(quote), round(p.selling_price, 2), round(p.tax_amount, 2), round(p.final_total, 2),
        )
        st.session_state["last_invoice_id"] = inv["id"]
        st.session_state["last_invoice_sig"] = signature
        st.rerun()

last = st.session_state.get("last_invoice_id")
if last and (inv := db.get_invoice(conn, last)):
    st.success(f"Invoice **{inv['number']}** saved for {inv['customer_name']} — total {inr(inv['total'])}.")
    b1, b2 = st.columns(2)
    b1.download_button("📄 Download invoice (PDF)", invoice.build_invoice_pdf(inv, settings),
                       file_name=f"{inv['number']}.pdf", mime="application/pdf", type="primary", width="stretch")
    b2.download_button("🔒 Download internal cost sheet (PDF)", invoice.build_cost_sheet_pdf(inv, settings),
                       file_name=f"{inv['number']}-cost-sheet.pdf", mime="application/pdf", width="stretch")
    st.caption("The cost sheet shows your costs and profit — keep it to yourself, don't send it to the customer.")
