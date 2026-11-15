"""New Order: pick one or more items -> cost breakdown -> profit & tax -> PDF invoice."""

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


# Each order row gets a permanent id, so its widgets keep their values when
# another row above it is removed (widget keys are built from the id).
if "order_rows" not in st.session_state:
    st.session_state.order_rows, st.session_state.next_row_id = [0], 1

# ---- 1. What and for whom --------------------------------------------------------------
st.subheader("1 · What is the customer ordering?")
order_items: list[costing.OrderItem] = []
for n, row_id in enumerate(st.session_state.order_rows, start=1):
    c1, c2, c3, c4 = st.columns([3, 1, 1, 0.5], vertical_alignment="bottom")
    rid = c1.selectbox(f"Item {n}", list(recipes), format_func=lambda i: recipes[i]["name"], key=f"recipe_{row_id}")
    recipe = db.get_recipe(conn, rid)
    yield_unit = units.normalize_unit(recipe["yield_unit"])
    unit_options = ORDER_UNITS[units.dimension_of(yield_unit)]
    # Keys include the recipe id: switching recipe resets quantity/unit to that recipe's own yield.
    qty = c2.number_input("Quantity", min_value=0.01,
                          value=float(recipe["yield_quantity"]) if yield_unit in unit_options else 1.0,
                          step=0.5 if unit_options[0] == "kg" else 1.0, key=f"qty_{row_id}_{rid}")
    order_unit = c3.selectbox("Unit", unit_options, key=f"unit_{row_id}_{rid}",
                              index=unit_options.index(yield_unit) if yield_unit in unit_options else 0)
    if len(st.session_state.order_rows) > 1 and c4.button("🗑️", key=f"remove_{row_id}", help="Remove this item"):
        st.session_state.order_rows.remove(row_id)
        st.rerun()
    order_items.append(costing.OrderItem(recipe, qty, order_unit))

if st.button("➕ Add another item"):
    st.session_state.order_rows.append(st.session_state.next_row_id)
    st.session_state.next_row_id += 1
    st.rerun()

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
    quote = costing.build_quote(order_items, fixed, profit, mode, tax, settings["rounding_step"])
if quote is None:
    st.stop()
p = quote.price
production_cost = sum(q.production_cost for q in quote.items)

with st.expander("🤔 Margin or markup? What's the difference?"):
    st.markdown(
        f"Both add profit on top of your cost (**{inr(production_cost)}** for this order), but they measure "
        f"'{profit:g}%' against different things:\n\n"
        "- **Markup** = a % of *your cost*. Price = cost × (1 + markup).\n"
        "- **Margin** = a % of *the selling price*. Price = cost ÷ (1 − margin).\n\n"
        "So the same number gives a different price:"
    )
    rows = []
    for m in ("markup", "margin"):
        try:
            price = costing.apply_profit(production_cost, profit, m)
            rows.append({"Method": m.title(), "Selling price (before tax)": inr(price),
                         "Your profit": inr(price - production_cost),
                         "Profit as % of price": f"{(price - production_cost) / price:.1%}" if price else "—"})
        except ValueError as exc:
            rows.append({"Method": m.title(), "Selling price (before tax)": str(exc)})
    st.table(pd.DataFrame(rows).set_index("Method"))
    st.caption("Most bakers think in margin ('I keep half of what the customer pays'); "
               "markup is quicker to calculate in your head.")

# ---- 3. Cost breakdown -------------------------------------------------------------------
st.subheader("3 · Cost breakdown")
for n, q in enumerate(quote.items, start=1):
    title = f"{q.recipe_name} — {q.order_text}: ingredients {inr(q.estimate.total)}"
    with st.expander(title, expanded=len(quote.items) == 1):
        st.caption(f"Recipe made **×{q.factor:g}**. Estimated weight {q.weight_kg:.2f} kg (used for per-kg costs). "
                   f"Includes {q.estimate.wastage_percent:g}% wastage ({inr(q.estimate.wastage_amount)}).")
        st.dataframe(
            pd.DataFrame([{
                "Ingredient": l.name,
                "Needed": f"{l.quantity:g} {l.unit}" + ("" if l.base_quantity is None or l.unit == l.base_unit
                                                         else f"  (= {l.base_quantity:,.0f} {l.base_unit})"),
                "Price": price_label(l.price_per_base_unit, l.base_unit),
                "Cost": l.cost,
                "Check": "⚠️ " + "; ".join(l.flags) if l.flags else "✅",
            } for l in q.estimate.lines]),
            hide_index=True, width="stretch", key=f"breakdown_{n}",
            column_config={"Cost": st.column_config.NumberColumn(format="₹%.2f")},
        )
for w in quote.warnings:
    st.warning(w, icon="⚠️")

left, right = st.columns(2)
with left:
    st.markdown("##### 🔒 Your cost sheet (internal)")
    sheet = [(f"Ingredients: {q.recipe_name}" if len(quote.items) > 1 else "Ingredients (incl. wastage)",
              q.estimate.total) for q in quote.items]
    sheet += [(f"+ {name}", amount) for name, amount in p.fixed_cost_lines]
    sheet += [("**Production cost**", production_cost),
              (f"Profit ({mode} {profit:g}%)", quote.subtotal - production_cost),
              ("**Selling price**", quote.subtotal)]
    st.markdown(money_table(sheet))
    if len(quote.items) > 1:
        st.caption("Fixed costs like packaging and delivery are counted once for the whole order.")
with right:
    st.markdown("##### 👀 What the customer sees")
    cust = [(f"{q.recipe_name} — {q.order_text}", q.amount) for q in quote.items]
    if len(quote.items) > 1:
        cust.append(("Subtotal", quote.subtotal))
    if tax:
        cust.append((f"{settings['tax_label']} @ {tax:g}%", quote.tax_amount))
    if abs(quote.round_off) >= 0.005:
        cust.append(("Round off", quote.round_off))
    cust.append(("**Total**", quote.final_total))
    st.markdown(money_table(cust))

m1, m2, m3 = st.columns(3)
m1.metric("Your cost", inr(production_cost))
m2.metric("Your profit", inr(quote.final_total - quote.tax_amount - production_cost),
          help="Includes the round-off, which goes to (or comes from) you.")
m3.metric("Customer pays", inr(quote.final_total, 0))

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
signature = (tuple((q.recipe_name, q.order_quantity, q.order_unit) for q in quote.items),
             customer.strip(), phone_clean, mode, profit, tax, quote.final_total)
already = st.session_state.get("last_invoice_sig") == signature
if st.button("🧾 Create invoice", type="primary", disabled=bool(missing) or already,
             help="Already created — change the order to make another." if already else None):
    with friendly_errors("Couldn't create the invoice"):
        inv = db.save_invoice(
            conn, customer, phone_clean, costing.invoice_line_items(quote), costing.cost_sheet(quote),
            quote.subtotal, quote.tax_amount, quote.final_total,
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
