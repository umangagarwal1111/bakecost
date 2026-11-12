# BakeCost: Costing and Invoicing for Home Bakers

**Live app (try it in your browser, no install or login):** https://bakecost.streamlit.app

**Source code:** https://github.com/umangagarwal1111/bakecost

---

## The problem
Home bakers and one-person home businesses often price their cakes by guesswork. Ingredient
prices change every month, recipes are written in cups and teaspoons while ingredients are bought
by the kilo, and costs like gas, packaging and their own time are easy to forget. Many under-charge
without realising it, and few have a proper invoice to give customers.

## Our solution
BakeCost is a simple app made for non-technical users. It:

- **Keeps an Ingredient Library** with current prices and a full price history. Prices older than 60 days are flagged.
- **Keeps a Recipe Book.** Each recipe lists what goes into one batch and how much that batch makes.
- **Reads shop bills from a photo** (camera or upload) to update prices, and **reads recipe photos** to add recipes. The user checks every line in an editable table before anything is saved.
- **Prices an order.** It scales the recipe to the order size, costs every ingredient, adds wastage and fixed costs (gas, packaging, labour, delivery), then profit and tax, and rounds to the nearest ₹5 or ₹10.
- **Explains margin and markup.** "50% profit" gives ₹1,141 under margin but ₹856 under markup for the same cake, and the app shows both side by side.
- **Creates a PDF invoice** for the customer, plus a separate internal cost sheet that the customer never sees.

**Example:** a 1 kg chocolate truffle cake costs ₹320 in ingredients and ₹571 including fixed costs.
At a 50% margin plus 5% GST, the customer pays ₹1,200.

## How it works
`Photo → clean-up → OCR → text parsing → fuzzy matching → user review → database`
`Recipe + order size → unit conversion → scaling → cost → fixed costs → profit → tax → PDF invoice`

We wrote the core logic ourselves rather than relying on libraries:

| Algorithm | What it does | Time complexity |
|---|---|---|
| Unit conversion | kg↔g, L↔ml, dozen→pcs; cups and spoons to grams using a density table (1 cup flour ≈ 125 g) | O(1) per conversion |
| Recipe scaling | Order ÷ recipe yield (500 g recipe, 1 kg order → ×2) | O(1) |
| Cost estimate | Σ(quantity × price per gram) × (1 + wastage%), flagging missing or old prices | O(n) ingredients |
| Pricing | Margin: cost ÷ (1 − m); Markup: cost × (1 + m); then tax and half-up rounding | O(f) fixed costs |
| Fuzzy matching | Matches "AMUL BUTTR 500G" to "Butter" with a confidence score; learns new bill names | O(B·I·A·L²) |
| Photo clean-up | Straightens tilted photos (projection profile) and converts them to black and white (Otsu's method) | O(pixels) |

## Technology
Python 3.11+ only: Streamlit (interface), SQLite (database), Tesseract OCR via pytesseract, Pillow,
and fpdf2 (PDF). If OCR isn't available, the app still works fully with typed entry.

## Testing
147 automated tests (pytest) cover unit conversion, scaling, costing, pricing, matching, text
parsing, the database, OCR on sample photos, the full order-to-PDF flow, and a headless run of every page.

## Try it in 2 minutes
1. Open the live app. It starts with demo data: 25 ingredients and 4 recipes.
2. **Scan a Bill** → *Use a sample* → `bill_photo_tilted.png` → **Read the text**. Check the table, then press **Update prices**.
3. **New Order** → *Chocolate Truffle Cake*, 1 kg, any customer name. Open "Margin or markup?".
4. Press **Create invoice**, then **Download invoice (PDF)**.

*Each visitor gets a private copy of the demo data, which resets when the tab is closed.*

## Limitations and future work
Handwritten recipes need manual correction. Cup-to-gram densities are averages (±10%). Each invoice
holds one item. Next steps: multi-item invoices, a rupee-symbol font in PDFs, and cloud backup.

## Tools used
Built with the help of Claude Code, an AI coding assistant, for code generation and testing.
