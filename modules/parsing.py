"""
Turn raw OCR text into structured rows, using regular expressions.

OCR gives us one long string. These parsers split it into lines and pull
out the pieces we need. They are deliberately forgiving: a line that can't
be understood is returned in `skipped` (bills) or with missing fields
(recipes) so the user can fix it in the review table, rather than being
silently dropped. Every function here is O(total characters) - each line is
scanned by a handful of regexes once.

Bill lines we understand (columns may be separated by any spaces):
    Maida 1kg            2     45.00     90.00     name, pack size, count, rate, amount
    Amul Butter 500g     1    280.00    280.00
    Eggs                30      7.00    210.00     no pack size -> 30 pcs
    Sugar 5 kg                          240.00     only an amount
    Milk 1L x 2                         128.00     "x 2" multiplier

Recipe lines we understand:
    2 cups maida            1 1/2 tsp baking powder       1/2 cup cocoa
    200 g sugar             3 eggs                        ½ cup milk
    Butter - 100 g          Vanilla essence: 1 tsp        - 2 tbsp oil
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from modules.units import is_known_unit, normalize_unit

# ---------------------------------------------------------------------------
# Shared regex pieces
# ---------------------------------------------------------------------------

# A unit word, as it may appear after a number. Longest alternatives first so
# "kgs" isn't read as "kg" + "s". Built from a fixed list rather than every
# alias, because single letters like "c" or "l" would match too much text.
_UNIT_WORDS = [
    "kilograms", "kilogram", "kgs", "kg", "grams", "gram", "gms", "gm", "grm", "g",
    "litres", "litre", "liters", "liter", "ltrs", "ltr", "lt", "l", "ml", "mls",
    "teaspoons", "teaspoon", "tsp", "tablespoons", "tablespoon", "tbsp", "tbs",
    "cups", "cup", "pieces", "piece", "pcs", "pc", "nos", "dozen", "doz", "oz", "lbs", "lb",
]
UNIT_RE = r"(?:" + "|".join(_UNIT_WORDS) + r")\b\.?"

# Money / plain numbers: "1,250.50", "45", "45.00"
NUMBER_RE = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"

UNICODE_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}

# Lines on a bill that are never purchased items.
BILL_SKIP_WORDS = re.compile(
    r"\b(sub\s*total|total|gst|cgst|sgst|igst|tax|discount|round|cash|change|balance|"
    r"bill|invoice|date|time|phone|mobile|ph|tel|thank|gstin|fssai|savings|"
    r"amount|qty|rate|item|particulars|customer|counter|cashier|upi|card|paid)\b",
    re.IGNORECASE,
)


def _to_float(text: str) -> float:
    return float(text.replace(",", ""))


# ---------------------------------------------------------------------------
# Bills
# ---------------------------------------------------------------------------


@dataclass
class BillLine:
    name: str
    quantity: float | None     # TOTAL bought, in `unit` (pack size x count)
    unit: str | None           # canonical unit, or None if the bill didn't say
    amount: float              # rupees paid for this line
    raw: str                   # original OCR text, shown to the user for checking


@dataclass
class BillParseResult:
    items: list[BillLine] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def parse_bill_line(line: str) -> BillLine | None:
    """Parse one bill line, or return None if it doesn't look like an item."""
    raw = line.strip()
    if not raw or not re.search(r"[A-Za-z]{2,}", raw) or BILL_SKIP_WORDS.search(raw):
        return None

    text = re.sub(r"(₹|rs\.?|inr)\s*", " ", raw, flags=re.IGNORECASE)

    # 1. Pack size: a number glued to (or followed by) a unit, e.g. "500g", "1 kg".
    pack_qty, pack_unit = None, None
    pack = re.search(rf"(?<![\d.])({NUMBER_RE})\s*({UNIT_RE})", text, re.IGNORECASE)
    if pack:
        pack_qty = _to_float(pack.group(1))
        pack_unit = normalize_unit(pack.group(2))
        text = text[: pack.start()] + " " + text[pack.end():]

    # 2. A multiplier written as "x 2" / "2 x" / "* 2".
    multiplier = None
    mult = re.search(r"(?:(?<=\s)[x×*]\s*(\d+)\b(?!\.))|(?:\b(\d+)\s*[x×*](?=\s))", text, re.IGNORECASE)
    if mult:
        multiplier = int(mult.group(1) or mult.group(2))
        text = text[: mult.start()] + " " + text[mult.end():]

    # 3. The item name is the text before the first remaining number.
    first_num = re.search(rf"(?<![A-Za-z])({NUMBER_RE})", text)
    name_part = text[: first_num.start()] if first_num else text
    name = re.sub(r"[^A-Za-z0-9&()/' -]", " ", name_part)
    name = re.sub(r"\s+", " ", name).strip(" -")
    if len(name) < 2:
        return None

    tokens = re.findall(NUMBER_RE, text[len(name_part):])
    if not tokens:
        return None
    numbers = [_to_float(t) for t in tokens]
    amount = numbers[-1]  # bills always end a line with the line total
    middle, middle_tokens = numbers[:-1], tokens[:-1]

    # 4. Count of packs: from "x 2"; else the qty column if qty x rate == amount;
    #    else, with one extra number, a whole number ("30") is a count and a
    #    decimal ("45.00") is a rate, so count = amount / rate.
    count = multiplier
    if count is None and len(middle) >= 2 and abs(middle[0] * middle[1] - amount) <= max(1.0, amount * 0.02):
        count = middle[0]
    elif count is None and len(middle) == 1 and middle[0] > 0:
        n = middle[0]
        if "." not in middle_tokens[0]:
            count = n
        elif abs(amount / n - round(amount / n)) < 0.01:
            count = round(amount / n)
    count = count or 1

    if pack_unit:
        quantity, unit = pack_qty * count, pack_unit
    elif count and count > 1:
        quantity, unit = float(count), "pcs"   # "Eggs 30 7.00 210.00" -> 30 pcs
    else:
        quantity, unit = None, None            # user must fill this in
    return BillLine(name=name, quantity=quantity, unit=unit, amount=amount, raw=raw)


def parse_bill(text: str) -> BillParseResult:
    """Split OCR text into lines and parse each one."""
    result = BillParseResult()
    for line in text.splitlines():
        if not line.strip():
            continue
        parsed = parse_bill_line(line)
        if parsed:
            result.items.append(parsed)
        else:
            result.skipped.append(line.strip())
    return result


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------


@dataclass
class RecipeLine:
    name: str
    quantity: float | None
    unit: str | None
    raw: str


@dataclass
class ParsedRecipe:
    title: str | None = None
    yield_quantity: float | None = None
    yield_unit: str | None = None
    lines: list[RecipeLine] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# "1 1/2", "1/2", "1.5", "2", "1½" - each may be followed by a unicode fraction.
_FRACTION_CHARS = "".join(UNICODE_FRACTIONS)
QTY_RE = rf"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)?[{_FRACTION_CHARS}]?"

_METHOD_HEADERS = re.compile(r"^\s*(method|instructions|directions|steps|procedure|how to make)\b", re.I)
_INGREDIENT_HEADERS = re.compile(r"^\s*(ingredients?|you will need)\s*:?\s*$", re.I)
_YIELD_RE = re.compile(
    rf"\b(makes|yield|yields|serves|quantity)\b\s*[:\-]?\s*({NUMBER_RE})\s*([a-z]+)?", re.I
)
_BULLET_RE = re.compile(r"^\s*(?:[-*•·o]|\d+[.)])\s+")


def parse_quantity(text: str) -> float | None:
    """'1 1/2' -> 1.5, '½' -> 0.5, '1½' -> 1.5, '2' -> 2.0, '' -> None."""
    text = text.strip()
    if not text:
        return None
    total = 0.0
    for ch, val in UNICODE_FRACTIONS.items():
        if ch in text:
            total += val
            text = text.replace(ch, "")
    text = text.strip()
    if " " in text:                              # mixed number "1 1/2"
        whole, frac = text.split(None, 1)
        return total + float(whole) + (parse_quantity(frac) or 0.0)
    if "/" in text:
        num, den = text.split("/", 1)
        if float(den) == 0:
            return None
        return total + float(num) / float(den)
    return total + (float(text) if text else 0.0)


def _clean_name(name: str) -> str:
    name = re.sub(r"\(.*?\)", " ", name)        # "(softened)"
    name = name.split(",")[0]                   # "butter, melted" -> "butter"
    name = re.sub(r"^\s*of\s+", "", name, flags=re.I)
    name = re.sub(r"[^A-Za-z&' -]", " ", name)
    return re.sub(r"\s+", " ", name).strip(" -:")


def parse_recipe_line(line: str) -> RecipeLine | None:
    """Parse '2 cups maida' or 'Butter - 100 g'. None if there's no ingredient."""
    raw = line.strip()
    text = _BULLET_RE.sub("", raw)
    if not re.search(r"[A-Za-z]{2,}", text):
        return None

    # Form A: quantity first - "1 1/2 tsp baking powder", "3 eggs".
    m = re.match(rf"^\s*({QTY_RE})\s*(?:({UNIT_RE})\s+)?(.+)$", text, re.I)
    if m and m.group(1).strip():
        qty = parse_quantity(m.group(1))
        unit = normalize_unit(m.group(2)) if m.group(2) else None
        name = _clean_name(m.group(3))
        if name:
            return RecipeLine(name, qty, unit or "pcs", raw)

    # Form B: name first - "Butter - 100 g", "Vanilla essence: 1 tsp".
    m = re.match(rf"^(.+?)\s*[-:–]?\s*({QTY_RE})\s*({UNIT_RE})?\s*$", text, re.I)
    if m and m.group(2).strip():
        qty = parse_quantity(m.group(2))
        unit = normalize_unit(m.group(3)) if m.group(3) else "pcs"
        name = _clean_name(m.group(1))
        if name:
            return RecipeLine(name, qty, unit, raw)

    # No quantity at all ("Salt a pinch"): keep it so the user can fill it in.
    name = _clean_name(text)
    return RecipeLine(name, None, None, raw) if name else None


def parse_recipe(text: str) -> ParsedRecipe:
    """Parse a whole recipe: title, yield, and ingredient lines.

    The first line without a quantity becomes the title. Everything after a
    'Method' / 'Instructions' header is ignored - those are steps, not
    ingredients, and would otherwise produce nonsense rows.
    """
    result = ParsedRecipe()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _METHOD_HEADERS.match(stripped):
            break
        if _INGREDIENT_HEADERS.match(stripped):
            continue

        y = _YIELD_RE.search(stripped)
        if y:
            result.yield_quantity = _to_float(y.group(2))
            unit_word = y.group(3) or "pcs"
            result.yield_unit = normalize_unit(unit_word) if is_known_unit(unit_word) else "pcs"
            continue

        if result.title is None and not re.match(rf"^\s*{QTY_RE}\s*\d", stripped) \
                and not re.search(r"\d", stripped):
            result.title = stripped.strip(":").title()
            continue

        parsed = parse_recipe_line(stripped)
        if parsed:
            result.lines.append(parsed)
        else:
            result.skipped.append(stripped)
    return result
