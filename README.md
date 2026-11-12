# 🧁 BakeCost

**Know what every cake costs you — and price it with confidence.**

BakeCost is a desktop/web app for home bakers and one-person home businesses. It keeps an
**Ingredient Library** (with prices) and a **Recipe Book** (with quantities), works out what an
order really costs to make, adds your fixed costs, profit and tax, and produces a clean
**PDF invoice** for the customer, plus a private cost sheet for you.

Photos do the typing: snap a **shop bill** to update ingredient prices, or a **recipe** to add
it to your Recipe Book. You always check what was read before anything is saved.

Built in **Python 3.11+** with Streamlit, SQLite, Tesseract OCR and fpdf2.

---

## Contents
1. [Install](#1-install)
2. [Run](#2-run)
3. [Architecture](#3-architecture)
4. [Data model](#4-data-model)
5. [Algorithms and their complexity](#5-algorithms-and-their-complexity)
6. [Known limitations](#6-known-limitations)
7. [2-minute demo script](#7-2-minute-demo-script)
8. [Project layout](#8-project-layout)

---

## 1. Install

### Step 1 — Python 3.11 or newer
Check with `python --version` (Windows) or `python3 --version` (macOS).
Get it from <https://www.python.org/downloads/> if needed. **Windows:** tick *"Add python.exe to PATH"* in the installer.

### Step 2 — Get the project and install the Python packages
Open a terminal (Windows: *PowerShell*; macOS: *Terminal*) in the `BAKECOST` folder:

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
> If PowerShell refuses to run the activate script, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then try again.

### Step 3 — Install Tesseract (the OCR program), optional
Tesseract reads text from photos. **Without it the app still works fully**: you type or paste
the bill/recipe text instead of scanning it.

**macOS** (with [Homebrew](https://brew.sh)):
```bash
brew install tesseract
```

**Windows**
1. Download the installer from the UB Mannheim build: <https://github.com/UB-Mannheim/tesseract/wiki>
   (e.g. `tesseract-ocr-w64-setup-5.x.x.exe`).
2. Run it and keep the default folder `C:\Program Files\Tesseract-OCR`.
3. That's it. BakeCost looks in that folder automatically. If you installed somewhere else, set
   an environment variable before starting the app:
   ```powershell
   $env:TESSERACT_CMD = "D:\Tools\Tesseract-OCR\tesseract.exe"
   ```

**Linux (Debian/Ubuntu)**: `sudo apt install tesseract-ocr`

Check it worked: `tesseract --version`. The *Scan a Bill* page also shows whether it was found.

---

## 2. Run

```bash
streamlit run app.py
```
Your browser opens at <http://localhost:8501>. On the **Dashboard**, press **Load demo data**
to try everything without any photos.

Other commands:

| Command | What it does |
|---|---|
| `pytest` | Run all 146 automated tests (OCR tests are skipped if Tesseract is missing) |
| `python seed.py` | Load demo data from the terminal (asks first; `--yes` to skip the question) |
| `python scripts/make_samples.py` | Re-create the synthetic sample bill/recipe images in `samples/` |

Data is stored in `data/bakecost.db` (a single SQLite file; back it up by copying it).
Set the `BAKECOST_DB` environment variable to use a different file.

### Host it online for others to try (free)
1. Push this folder to a GitHub repository (private is fine).
2. Go to <https://share.streamlit.io>, sign in with GitHub, press **Create app**, and pick the
   repository, branch `main` and main file `app.py`.
3. Under **Advanced settings**, choose Python **3.12** and paste this into **Secrets**:
   ```toml
   BAKECOST_DEMO = "1"
   ```
4. Press **Deploy**. `packages.txt` makes the server install Tesseract automatically.

`BAKECOST_DEMO=1` turns on **demo mode**: every visitor gets their own private copy of the demo
data, held in memory and reset when they close the tab, so testers never overwrite each other's work.
Don't use demo mode for real business data; it is never saved.

---

## 3. Architecture

```
                         ┌──────────────────────────────────────────────┐
   Browser (Streamlit)   │  app.py  ── navigation menu                  │
                         │  views/  ── one file per page (UI only)      │
                         │   dashboard · new_order · ingredients ·      │
                         │   recipes · bill_scan · recipe_scan · settings│
                         └───────┬───────────────┬───────────────┬──────┘
                                 │               │               │
           ┌─────────────────────▼──┐   ┌────────▼────────┐  ┌───▼──────────────┐
  Pure     │ costing.py             │   │ matching.py     │  │ invoice.py       │
  logic    │  scale → cost → fixed  │   │  bill text →    │  │  customer PDF +  │
  (tested) │  → profit → tax → round│   │  ingredient     │  │  internal sheet  │
           └──────────┬─────────────┘   └────────▲────────┘  └──────────────────┘
                      │ uses                     │
           ┌──────────▼─────────────┐   ┌────────┴────────┐  ┌──────────────────┐
           │ units.py               │   │ parsing.py      │◄─┤ ocr.py           │
           │  g/kg/ml/L/cup/tsp/pcs │   │  regex: text →  │  │  clean-up photo  │
           │  + density table       │   │  rows           │  │  → Tesseract     │
           └────────────────────────┘   └─────────────────┘  │  (swappable)     │
                                                             └──────────────────┘
           ┌────────────────────────────────────────────────────────────────────┐
  Storage  │ db.py ── the only file with SQL ──► data/bakecost.db (SQLite)      │
           └────────────────────────────────────────────────────────────────────┘

  Bill scan:   photo ─► ocr ─► parsing ─► matching ─► EDITABLE REVIEW ─► db (prices + history)
  Recipe scan: photo ─► ocr ─► parsing ─► matching ─► EDITABLE REVIEW ─► db (recipe)
  New order:   db ─► costing (+units) ─► preview ─► db (invoice) ─► invoice (PDF)
```

**Why this split?** The logic modules (`units`, `costing`, `matching`, `parsing`) never touch
the screen or the database, so they can be tested on their own with exact numbers. The pages
only arrange widgets. `ocr.py` hides the OCR engine behind three functions, so Tesseract can
be replaced (e.g. by a cloud OCR API) by writing one small class and calling `ocr.set_engine()`.

---

## 4. Data model

| Table | Columns | Notes |
|---|---|---|
| `ingredients` | id, name, aliases, base_unit (`g`/`ml`/`pcs`), current_price_per_base_unit, last_updated, category | Price is ₹ per **g / ml / piece**; shown to users per kg / L / pc |
| `price_history` | ingredient_id, price_per_base_unit, quantity_bought, source (`bill`/`manual`), date | One row for every price change |
| `recipes` | id, name, yield_quantity, yield_unit, wastage_percent (default 5), notes | e.g. 1 kg cake, 12 pcs brownies |
| `recipe_items` | recipe_id, ingredient_id, quantity, unit | Unit as written: cups, tsp, g… |
| `fixed_costs` | name, type (`per_order`/`per_kg`/`percent_of_cost`), value, active | Gas, packaging, labour… |
| `settings` | key, value | Business name, address, GSTIN, tax %, rounding, invoice counter… |
| `invoices` | number, date, customer name/phone, line_items_json, cost_breakdown_json, subtotal, tax, total | Snapshot: reprints stay identical even after prices change |

Integrity rules are in the schema itself: `CHECK` constraints (no negative prices, valid units
and types), `UNIQUE` names (case-insensitive), and foreign keys (an ingredient used in a recipe
can't be deleted). Price updates and invoice numbering run inside transactions.

The tax rate is **not hard-coded**: it defaults to 0 % and each baker sets their own in Settings
(the demo data uses 5 % purely as an example).

---

## 5. Algorithms and their complexity

### 5.1 Unit conversion (`modules/units.py`)
Every unit belongs to a **dimension** (mass, volume or count) and has a **factor** to that
dimension's base unit: `kg → 1000 g`, `cup → 240 ml`, `tbsp → 15 ml`, `tsp → 5 ml`, `dozen → 12 pcs`.

1. Normalise the spelling with a lookup table: `"Kgs."`, `"kilo"` → `kg`.
2. **Same dimension** → `value × factor_from ÷ factor_to`.
3. **Volume ↔ mass** (e.g. "1 cup maida" to grams) → go through grams using the ingredient's
   **density** in g/ml from a documented table (flour 0.52, sugar 0.83, butter 0.95, cocoa 0.35,
   milk 1.03, …; derived from standard "1 cup = X g" baking charts). The most specific name wins:
   "icing sugar" (0.50) beats "sugar" (0.83).
4. **Count ↔ mass** (e.g. "2 eggs" to grams) → typical piece weight (egg = 50 g).
5. Anything else raises `UnitConversionError` with a message that says what to do,
   e.g. *"Can't convert cup to g for 'Mystery Powder': its density is unknown. Enter the quantity in g instead."*

**Complexity:** O(1) per conversion (dictionary lookups); density lookup O(D) for D ≈ 60 table entries.

### 5.2 Recipe scaling (`costing.scale_factor`)
`factor = order quantity (converted into the recipe's yield unit) ÷ recipe yield`.
- 500 g sponge, order 1 kg → 1000 / 500 = **×2**
- 12 brownies, order 2 dozen → 24 / 12 = **×2**

Weight-based recipes can't be ordered in pieces (and vice versa); that raises a clear error.
**Complexity:** O(1).

### 5.3 Cost estimate (`costing.estimate_cost`)
```
cost = Σ ( quantity × factor  →converted to base unit  × price per base unit ) × (1 + wastage%)
```
One pass over the recipe rows. Rows that can't be costed (no price, impossible unit) are costed
at ₹0 and **flagged** rather than stopping everything, so the baker sees the rest of the estimate
and exactly which row to fix. Prices older than **60 days** are flagged too.
**Complexity:** O(n) for n ingredients.

### 5.4 Pricing formula (`costing.price_order`)
```
ingredient cost      = (from 5.3, includes wastage)
fixed costs          = Σ  per_order: value
                          per_kg:    value × order weight (kg)
                          percent_of_cost: value% × ingredient cost
production cost      = ingredient cost + fixed costs
selling price        = MARKUP: production cost × (1 + m)
                       MARGIN: production cost ÷ (1 − m)          (m must be < 100 %)
tax                  = selling price × tax%
total                = round_half_up(selling price + tax, to nearest ₹5 or ₹10)
round off            = total − (selling price + tax)              (printed on the invoice)
```
**Margin vs markup:** at a cost of ₹100, "50 %" **markup** gives ₹150 (profit is ⅓ of the
price), while "50 %" **margin** gives ₹200 (profit is ½ of the price). The New Order page shows
both side by side with the real numbers. Rounding is half-up (Python's `round()` uses banker's
rounding, which surprises people with money: `round(2.5) == 2`).
Order weight for per-kg costs: the order itself for kg recipes; for piece recipes, the sum of the
g/ml ingredients after scaling. **Complexity:** O(f) for f fixed costs.

### 5.5 Fuzzy bill-name matching (`modules/matching.py`)
Bills say "AMUL BUTTR 500G PKT"; the library says "Butter". For each library name and alias:
1. **Normalise:** lowercase, drop digits, units and filler words (`pkt`, `loose`, `premium`…) → `["amul", "buttr"]`.
2. **Exact** normalised match → confidence 1.0.
3. **Containment:** every word of the library name appears in the bill line, allowing small
   typos per word (word similarity ≥ 0.8 via `difflib.SequenceMatcher`) → 0.85 + 0.14 × coverage.
   "buttr" ≈ "butter", so → 0.92.
4. Otherwise **fuzzy:** ½ × character similarity (handles OCR typos) + ½ × word overlap
   (handles different word order).

The best score over all names/aliases is the confidence. Suggestions **≥ 75 %** start ticked;
lower ones start un-ticked; below 45 % nothing is suggested. **Nothing is saved until the user
presses Save.** "Remember these bill names" adds the bill's wording as an alias, so the same
item matches at 100 % next time: the system learns from its corrections.

**Complexity:** O(B × I × A × L²) for B bill lines, I ingredients, A aliases each, names of length L
(SequenceMatcher is quadratic in the worst case). With 25–200 ingredients this is a few milliseconds.

### 5.6 Bill and recipe parsing (`modules/parsing.py`)
Regular expressions pick out, per line: the **pack size** (`500g`, `1 L`), a **multiplier**
(`x 2`), the **name** (text before the first number) and the **amount** (last number on the line).
The pack count comes from the qty column when `qty × rate = amount`; a lone whole number is a
count ("Eggs 30 … 210.00" → 30 pcs), a lone decimal is a rate (count = amount ÷ rate). Lines
with words like TOTAL, GST, Bill No are skipped and shown separately. Recipes understand
`1 1/2 cups`, `½ cup`, `200g butter, softened`, `Butter - 100 g`, a "Makes 12" yield line, and
stop at "Method". **Complexity:** O(total characters).

### 5.7 Photo clean-up before OCR (`modules/ocr.py`)
1. EXIF rotation fix → grayscale → resize to ~1600 px wide → autocontrast. O(P) for P pixels.
2. **Deskew by projection profile:** rotate a small copy through −10°…+10° in 1° steps, then
   ±1° in 0.1° steps; for each angle sum the ink in every pixel row. When text lines are level,
   rows alternate between "full of ink" and "empty", so the sum of squared differences between
   neighbouring rows peaks. O(A × p): ~42 angles on a ≤600 px thumbnail.
3. **Otsu threshold:** choose the grey level that maximises the between-class variance
   w₀·w₁·(μ₀ − μ₁)² of "ink" vs "paper"; with cumulative sums all 256 levels cost O(256) after
   an O(P) histogram.

On the sample "phone photo" (tilted 4°, uneven light, noise) the deskew finds −4.0° and every
line is read correctly.

---

## 6. Known limitations
- **Handwriting:** Tesseract is trained on print. Handwritten recipes usually need manual
  correction; the app says so and lets you type instead.
- **Real-world bills vary a lot:** thermal-paper fade, multi-line item names, or columns in
  unusual orders can confuse the parser. Every line goes through the editable review table, so
  mistakes are caught, not saved.
- **Brand vs product words:** the matcher can't know that "Peanut Butter" is not "Butter" (it
  scores 0.92). Review catches this; adding a "Peanut Butter" ingredient fixes it permanently.
- **Densities are averages:** "1 cup flour" weighs 120–135 g depending on how it's scooped
  (±10 %). For exact costing, write recipes in grams.
- **Per-kg costs for piece recipes** use the raw ingredient weight, which slightly over-states
  the baked weight (moisture is lost in the oven), so it errs on the side of not under-charging.
- **One item per invoice** from the New Order page (the data model stores a list of line items,
  so multi-item invoices are a UI addition).
- **PDF uses "Rs."** instead of "₹": the built-in PDF fonts don't include the rupee sign, and
  non-Latin characters in names (e.g. Hindi) print as "?". Bundling a Unicode TTF font would fix both.
- **Single user, single device:** SQLite file on one machine; no login or cloud sync. Back up
  `data/bakecost.db` by copying it.
- Not tax or accounting advice: check your GST registration and rate with an accountant.

---

## 7. 2-minute demo script

| Time | Do | Say |
|---|---|---|
| 0:00 | Open the **Dashboard**, press **Load demo data**. | "A home baker's library: 25 ingredients at today's prices, 4 recipes, her fixed costs." |
| 0:15 | **Ingredients**: point at Walnuts ⏰ *75 days old*. Open *Edit / price history* for Butter. | "Prices per kg, a history of every change, and old prices flagged before they cost her money." |
| 0:30 | **Scan a Bill** → *Use a sample* → `bill_photo_tilted.png` → **Read the text**. | "A crooked phone photo. We straighten it (deskew), clean it (Otsu), then read it with OCR." Open *See the cleaned-up image*. |
| 0:50 | Scroll to the review table. | "Each line is matched to an ingredient with a confidence score. Nothing is saved until she checks it. Butter went from ₹560 to ₹570/kg." Press **Update 7 prices**. |
| 1:05 | **New Order** → *Chocolate Truffle Cake*, 1 kg, customer *Priya Sharma*. | "One recipe batch makes 1 kg, so we scale ×1. Cups and teaspoons are converted to grams with a density table." |
| 1:20 | Open **Margin or markup?** | "50 % markup gives ₹856, 50 % margin gives ₹1,141. Same number, very different price. We let the baker choose." |
| 1:35 | Point at the two tables. | "Left: her private cost sheet with gas, labour, packaging and profit. Right: only what the customer sees, with GST and a round-off to the nearest ₹10." |
| 1:45 | **Create invoice** → **Download invoice (PDF)** and open it. | "A clean, numbered invoice. The internal cost sheet is a separate PDF that never goes to the customer." |
| 1:55 | Back to the **Dashboard**. | "Recent invoices and the most expensive ingredients, at a glance." |

---

## 8. Project layout
```
BAKECOST/
├── app.py                 Streamlit entry point + navigation
├── seed.py                Demo data (CLI and "Load demo data" button)
├── requirements.txt       streamlit, pandas, numpy, Pillow, pytesseract, fpdf2, pytest
├── pytest.ini
├── modules/
│   ├── db.py              SQLite schema and every query
│   ├── units.py           Unit conversion + density table
│   ├── costing.py         Scaling, cost estimate, fixed costs, margin/markup, tax, rounding
│   ├── matching.py        Fuzzy bill-name → ingredient matching
│   ├── parsing.py         Regex parsers for bill and recipe text
│   ├── ocr.py             Photo clean-up + swappable OCR engine (Tesseract by default)
│   └── invoice.py         Customer invoice and internal cost sheet PDFs (fpdf2)
├── views/                 One Streamlit page per file (+ common.py, scan_common.py helpers)
├── tests/                 146 pytest tests: units, costing, matching, parsing, db, OCR,
│                          end-to-end order → PDF, and a headless run of every page
├── scripts/make_samples.py   Generates the synthetic sample images
├── samples/               bill_grocery.png, bill_photo_tilted.png, recipe_cupcakes.png
└── data/bakecost.db       Created on first run
```
