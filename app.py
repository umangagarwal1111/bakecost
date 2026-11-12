"""
BakeCost - cost, price and invoice home-baked products.

Run with:   streamlit run app.py

This file only sets up the page and the navigation menu. Each page lives in
views/ and all real logic lives in modules/ (see README for the architecture).
"""

import streamlit as st

from views.common import demo_mode

st.set_page_config(page_title="BakeCost", page_icon="🧁", layout="wide")

if demo_mode():
    st.sidebar.info("**Demo version.** It starts with sample data. Your changes are private to "
                    "this browser tab and reset when you close or reload it.", icon="🧪")

pages = {
    "": [
        st.Page("views/dashboard.py", title="Dashboard", icon="🏠", default=True),
        st.Page("views/new_order.py", title="New Order", icon="🧾"),
    ],
    "Library": [
        st.Page("views/ingredients.py", title="Ingredients", icon="🥚"),
        st.Page("views/recipes.py", title="Recipe Book", icon="📖"),
    ],
    "Scan": [
        st.Page("views/bill_scan.py", title="Scan a Bill", icon="📷"),
        st.Page("views/recipe_scan.py", title="Scan a Recipe", icon="📝"),
    ],
    "Setup": [
        st.Page("views/settings.py", title="Fixed Costs & Settings", icon="⚙️"),
    ],
}

st.navigation(pages).run()
