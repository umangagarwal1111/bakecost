"""Fixed costs (gas, packaging, labour ...) and business settings."""

import re

import pandas as pd
import streamlit as st

from modules import db
from views.common import flash, friendly_errors, get_conn, show_flash

# 15 characters: 2-digit state code, 10-character PAN, entity number, 'Z', checksum.
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

conn = get_conn()
st.title("⚙️ Fixed Costs & Settings")
show_flash()

# --------------------------------------------------------------------------- fixed costs
st.subheader("Fixed costs")
st.markdown(
    "Costs that aren't ingredients but still come out of your pocket for every order:\n"
    "- **per_order** – a flat amount for each order (a cake box, delivery)\n"
    "- **per_kg** – grows with the size of the order (gas, electricity, your time)\n"
    "- **percent_of_cost** – a % of the ingredient cost (small extras, spoilage)"
)
TYPES = ["per_order", "per_kg", "percent_of_cost"]
fixed = pd.DataFrame(db.list_fixed_costs(conn), columns=["name", "type", "value", "active"])
fixed["active"] = fixed["active"].astype(bool)
edited = st.data_editor(
    fixed, num_rows="dynamic", width="stretch", key="fixed_costs_editor",
    column_config={
        "name": st.column_config.TextColumn("Name", required=True),
        "type": st.column_config.SelectboxColumn("Type", options=TYPES, required=True, default="per_order"),
        "value": st.column_config.NumberColumn("Value (₹ or %)", min_value=0.0, step=1.0, required=True),
        "active": st.column_config.CheckboxColumn("Use it", default=True),
    },
)
if st.button("💾 Save fixed costs", type="primary"):
    rows = [r for r in edited.to_dict("records") if str(r.get("name") or "").strip()]
    with friendly_errors("Couldn't save fixed costs"):
        for r in rows:
            r["active"] = bool(r.get("active")) if not pd.isna(r.get("active")) else True
            r["value"] = None if pd.isna(r.get("value")) else r["value"]
        db.replace_fixed_costs(conn, rows)
        flash(f"Saved {len(rows)} fixed cost(s).")
        st.rerun()

st.divider()

# --------------------------------------------------------------------------- settings
s = db.get_settings(conn)
st.subheader("Business details & pricing")
with st.form("settings"):
    c1, c2 = st.columns(2)
    business_name = c1.text_input("Business name *", s["business_name"])
    phone = c2.text_input("Phone", s["phone"])
    address = st.text_area("Address (printed on invoices)", s["address"], height=68)
    gstin = c1.text_input("GSTIN (optional)", s["gstin"],
                          help="Only if you are GST-registered. When filled in and tax is above 0, "
                               "the PDF is titled 'TAX INVOICE'.")
    st.markdown("**Tax** — ask your accountant which rate applies to your products. "
                "Set 0 if you are not registered for GST.")
    t1, t2 = st.columns(2)
    tax_label = t1.text_input("Tax name", s["tax_label"])
    tax_percent = t2.number_input("Tax %", min_value=0.0, max_value=100.0, value=float(s["tax_percent"]), step=0.5)

    st.markdown("**Prices & invoices**")
    p1, p2, p3 = st.columns(3)
    steps = [1, 5, 10]
    rounding = p1.selectbox("Round final price to nearest", steps,
                            index=steps.index(s["rounding_step"]) if s["rounding_step"] in steps else 1,
                            format_func=lambda v: "₹1 (no rounding)" if v == 1 else f"₹{v}")
    modes = ["margin", "markup"]
    mode = p2.selectbox("Default profit method", modes, index=modes.index(s["default_profit_mode"]))
    profit = p3.number_input("Default profit %", min_value=0.0, max_value=95.0,
                             value=float(s["default_profit_percent"]), step=5.0)
    i1, i2 = st.columns(2)
    prefix = i1.text_input("Invoice number prefix", s["invoice_prefix"])
    counter = i2.number_input("Next invoice number", min_value=1, value=int(s["invoice_counter"]), step=1)

    if st.form_submit_button("💾 Save settings", type="primary"):
        if not business_name.strip():
            st.error("Business name is required - it goes at the top of every invoice.")
        elif gstin.strip() and not GSTIN_RE.match(gstin.strip().upper()):
            st.error("That GSTIN doesn't look right. It has 15 characters, like 29ABCDE1234F1Z5. "
                     "Leave it empty if you aren't GST-registered.")
        else:
            db.save_settings(conn, {
                "business_name": business_name.strip(), "phone": phone.strip(), "address": address.strip(),
                "gstin": gstin.strip().upper(), "tax_label": tax_label.strip() or "Tax", "tax_percent": tax_percent,
                "rounding_step": rounding, "default_profit_mode": mode, "default_profit_percent": profit,
                "invoice_prefix": prefix.strip(), "invoice_counter": int(counter),
            })
            flash("Settings saved.")
            st.rerun()
st.caption("Currency: Indian Rupee (INR).")
