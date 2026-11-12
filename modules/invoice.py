"""
PDF generation with fpdf2: the customer invoice and the internal cost sheet.

Two separate documents on purpose. The customer sees only what they are
paying for (item, price, tax, total). The cost sheet - ingredient costs,
overheads and profit - is for the baker's eyes only and must never leak
onto a customer's bill.

Note on the rupee sign: fpdf2's built-in fonts only cover Latin-1, which has
no "₹". Rather than shipping a font file we print "Rs.", which every Indian
customer reads naturally. _safe() also replaces any other non-Latin-1
character (e.g. a name typed in Hindi) so PDF generation can never crash.
"""

from __future__ import annotations

from fpdf import FPDF
from fpdf.enums import XPos, YPos

NEXT_LINE = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}


def _safe(text) -> str:
    text = str(text if text is not None else "").replace("₹", "Rs.").replace("–", "-").replace("×", "x")
    return text.encode("latin-1", "replace").decode("latin-1")


def money(amount: float) -> str:
    """1234.5 -> 'Rs. 1,234.50' (negative round-offs keep their sign)."""
    sign = "-" if amount < 0 else ""
    return f"{sign}Rs. {abs(amount):,.2f}"


class _Doc(FPDF):
    def __init__(self, footer_text: str):
        super().__init__(format="A4")
        self.footer_text = footer_text
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(18, 16, 18)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120)
        self.cell(0, 6, _safe(self.footer_text), align="C")


def _header(pdf: _Doc, settings: dict, title: str) -> None:
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(90, 50, 30)
    pdf.cell(0, 9, _safe(settings.get("business_name") or "My Home Bakery"), **NEXT_LINE)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(60)
    for line in (settings.get("address"), settings.get("phone") and f"Phone: {settings['phone']}",
                 settings.get("gstin") and f"GSTIN: {settings['gstin']}"):
        if line:
            pdf.multi_cell(0, 4.5, _safe(line), **NEXT_LINE)
    pdf.ln(3)
    pdf.set_draw_color(200, 170, 140)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(0)
    pdf.cell(0, 8, _safe(title), **NEXT_LINE)


def _row(pdf: _Doc, cols: list[tuple[str, float, str]], bold: bool = False, fill: bool = False,
         height: float = 7.5) -> None:
    """One table row: list of (text, width_mm, align)."""
    pdf.set_font("Helvetica", "B" if bold else "", 10)
    if fill:
        pdf.set_fill_color(245, 235, 225)
    for i, (text, width, align) in enumerate(cols):
        last = i == len(cols) - 1
        pdf.cell(width, height, _safe(text), border="B", align=align, fill=fill,
                 **(NEXT_LINE if last else {}))


def build_invoice_pdf(invoice: dict, settings: dict) -> bytes:
    """Customer-facing invoice.

    `invoice` needs: number, date, customer_name, customer_phone,
    line_items [{description, quantity_text, amount}], subtotal,
    tax_amount, total, and in cost_breakdown: tax_percent, round_off.
    """
    pdf = _Doc("Thank you for your order!")
    pdf.add_page()
    tax_percent = invoice.get("cost_breakdown", {}).get("tax_percent", 0)
    round_off = invoice.get("cost_breakdown", {}).get("round_off", 0.0)
    _header(pdf, settings, "TAX INVOICE" if tax_percent and settings.get("gstin") else "INVOICE")

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(95, 6, _safe(f"Invoice No: {invoice['number']}"))
    pdf.cell(0, 6, _safe(f"Date: {invoice['date']}"), align="R", **NEXT_LINE)
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Bill to:", **NEXT_LINE)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5.5, _safe(invoice["customer_name"]), **NEXT_LINE)
    if invoice.get("customer_phone"):
        pdf.cell(0, 5.5, _safe(f"Phone: {invoice['customer_phone']}"), **NEXT_LINE)
    pdf.ln(5)

    widths = (100, 34, 40)
    _row(pdf, [("Item", widths[0], "L"), ("Qty", widths[1], "C"), ("Amount", widths[2], "R")], bold=True, fill=True)
    for item in invoice["line_items"]:
        _row(pdf, [(item["description"], widths[0], "L"), (item.get("quantity_text", ""), widths[1], "C"),
                   (money(item["amount"]), widths[2], "R")])
    pdf.ln(3)

    label_w, value_w = widths[0] + widths[1], widths[2]
    _row(pdf, [("Subtotal", label_w, "R"), (money(invoice["subtotal"]), value_w, "R")])
    if tax_percent:
        label = f"{settings.get('tax_label') or 'Tax'} @ {tax_percent:g}%"
        _row(pdf, [(label, label_w, "R"), (money(invoice["tax_amount"]), value_w, "R")])
    if abs(round_off) >= 0.005:
        _row(pdf, [("Round off", label_w, "R"), (money(round_off), value_w, "R")])
    _row(pdf, [("TOTAL", label_w, "R"), (money(invoice["total"]), value_w, "R")], bold=True, fill=True)
    return bytes(pdf.output())


def build_cost_sheet_pdf(invoice: dict, settings: dict) -> bytes:
    """Internal cost sheet for one order - never give this to the customer."""
    cb = invoice["cost_breakdown"]
    pdf = _Doc("INTERNAL - not for the customer")
    pdf.add_page()
    _header(pdf, settings, f"Cost sheet for {invoice['number']} (internal)")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5.5, _safe(
        f"{cb.get('recipe_name', '')} - order {cb.get('order_text', '')} "
        f"(recipe made x{cb.get('scale_factor', 1):.2f})  |  Customer: {invoice['customer_name']}  |  {invoice['date']}"
    ), **NEXT_LINE)
    pdf.ln(3)

    w = (64, 34, 40, 36)
    _row(pdf, [("Ingredient", w[0], "L"), ("Quantity", w[1], "R"), ("Price", w[2], "R"), ("Cost", w[3], "R")],
         bold=True, fill=True, height=6.2)
    for line in cb.get("lines", []):
        _row(pdf, [(line["name"], w[0], "L"), (line["quantity_text"], w[1], "R"),
                   (line["price_text"], w[2], "R"), (money(line["cost"]), w[3], "R")], height=6.2)
    pdf.ln(3)

    lw, vw = w[0] + w[1] + w[2], w[3]
    rows = [("Ingredients subtotal", cb["ingredients_subtotal"]),
            (f"Wastage @ {cb['wastage_percent']:g}%", cb["wastage_amount"]),
            ("Ingredient cost", cb["ingredient_cost"])]
    rows += [(f"  + {name}", amount) for name, amount in cb.get("fixed_cost_lines", [])]
    rows += [("Production cost", cb["production_cost"]),
             (f"Profit ({cb['profit_mode']} {cb['profit_percent']:g}%)", cb["profit_amount"]),
             ("Selling price (before tax)", cb["selling_price"]),
             (f"Tax @ {cb['tax_percent']:g}%", cb["tax_amount"]),
             ("Round off", cb["round_off"])]
    for label, amount in rows:
        _row(pdf, [(label, lw, "R"), (money(amount), vw, "R")], height=6.2)
    _row(pdf, [("Customer pays", lw, "R"), (money(cb["final_total"]), vw, "R")], bold=True, fill=True, height=6.2)

    if cb.get("warnings"):
        pdf.ln(5)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "Warnings", **NEXT_LINE)
        pdf.set_font("Helvetica", "", 9)
        for wtext in cb["warnings"]:
            pdf.multi_cell(0, 5, _safe(f"- {wtext}"), **NEXT_LINE)
    return bytes(pdf.output())
