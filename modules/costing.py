"""
Recipe scaling, cost estimation and selling-price calculation.

This module is pure logic: it never touches the database or the UI. Pages
load data with db.py, hand plain Python objects to these functions and show
the results. Keeping it pure is what makes it easy to unit-test.

The full pricing pipeline for one order is:

    1. scale_factor()         order size / recipe yield            O(1)
    2. estimate_cost()        sum of (scaled qty x price) + wastage O(n) for n ingredients
    3. apply_fixed_costs()    gas, packaging, labour ...           O(f) for f fixed costs
    4. apply_profit()         margin or markup                     O(1)
    5. apply_tax_and_round()  tax %, then round to nearest Rs 5/10 O(1)

`price_order()` runs steps 3-5 and returns a single PriceBreakdown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime

from modules import units
from modules.units import UnitConversionError

STALE_PRICE_DAYS = 60  # a price older than this gets a warning flag

# ---------------------------------------------------------------------------
# Small data containers (dataclasses keep the code readable for beginners)
# ---------------------------------------------------------------------------


@dataclass
class IngredientLine:
    """One recipe row joined with its ingredient's library data."""

    name: str
    quantity: float              # as written in the recipe
    unit: str                    # as written in the recipe
    base_unit: str               # g / ml / pcs (how the library stores it)
    price_per_base_unit: float | None
    last_updated: str | None = None  # ISO date "YYYY-MM-DD"


@dataclass
class CostLine:
    """One row of the per-ingredient cost breakdown table."""

    name: str
    quantity: float              # scaled, in the recipe's unit
    unit: str
    base_quantity: float | None  # scaled, in the library's base unit
    base_unit: str
    price_per_base_unit: float | None
    cost: float
    flags: list[str] = field(default_factory=list)


@dataclass
class CostEstimate:
    lines: list[CostLine]
    ingredients_subtotal: float  # before wastage
    wastage_percent: float
    wastage_amount: float
    total: float                 # ingredients_subtotal + wastage_amount

    @property
    def warnings(self) -> list[str]:
        return [f"{line.name}: {flag}" for line in self.lines for flag in line.flags]


@dataclass
class FixedCost:
    name: str
    type: str    # "per_order" | "per_kg" | "percent_of_cost"
    value: float


@dataclass
class PriceBreakdown:
    ingredient_cost: float
    fixed_cost_lines: list[tuple[str, float]]
    fixed_costs_total: float
    production_cost: float       # ingredient_cost + fixed costs
    profit_mode: str             # "margin" | "markup"
    profit_percent: float
    profit_amount: float
    selling_price: float         # before tax
    tax_percent: float
    tax_amount: float
    total_before_rounding: float
    round_off: float             # can be negative
    final_total: float           # what the customer pays


# ---------------------------------------------------------------------------
# 1. Scaling
# ---------------------------------------------------------------------------


def scale_factor(yield_quantity: float, yield_unit: str, order_quantity: float, order_unit: str) -> float:
    """How many times the recipe must be made to fill the order.

    The order is first converted into the recipe's yield unit, so a recipe
    that yields 500 g and an order of 1 kg gives 1000 / 500 = 2.0, and a
    recipe for 12 cupcakes with an order of 2 dozen gives 24 / 12 = 2.0.

    Mixing a weight-based yield with a piece-based order (or vice versa)
    raises UnitConversionError, because "3 pieces of a 1 kg cake" has no
    single right answer.
    """
    if yield_quantity is None or yield_quantity <= 0:
        raise ValueError("The recipe's yield must be more than zero.")
    if order_quantity is None or order_quantity <= 0:
        raise ValueError("The order quantity must be more than zero.")
    if units.dimension_of(yield_unit) != units.dimension_of(order_unit):
        raise UnitConversionError(
            f"This recipe is measured in {yield_unit}, but the order is in {order_unit}. "
            f"Please order in {units.dimension_of(yield_unit)} units."
        )
    order_in_yield_unit = units.convert(order_quantity, order_unit, yield_unit)
    return order_in_yield_unit / yield_quantity


# ---------------------------------------------------------------------------
# 2. Cost estimate
# ---------------------------------------------------------------------------


def _days_since(iso_date: str | None, today: date) -> int | None:
    if not iso_date:
        return None
    try:
        return (today - datetime.fromisoformat(iso_date[:10]).date()).days
    except ValueError:
        return None


def estimate_cost(
    lines: list[IngredientLine],
    factor: float = 1.0,
    wastage_percent: float = 5.0,
    today: date | None = None,
) -> CostEstimate:
    """Estimate ingredient cost of making a recipe `factor` times.

    cost = SUM(scaled qty in base units x price per base unit) x (1 + wastage%)

    We never stop on a problem row: an unpriced or unconvertible ingredient
    is costed at 0 and flagged, so the baker still sees the rest of the
    estimate and knows exactly which row to fix. One pass over the rows: O(n).
    """
    today = today or date.today()
    cost_lines: list[CostLine] = []
    subtotal = 0.0

    for item in lines:
        flags: list[str] = []
        scaled_qty = item.quantity * factor
        base_qty: float | None = None
        cost = 0.0

        try:
            base_qty = units.to_base(scaled_qty, item.unit, item.base_unit, item.name)
        except UnitConversionError as exc:
            flags.append(f"unit problem - {exc}")

        if item.price_per_base_unit is None:
            flags.append("no price in the library")
        else:
            age = _days_since(item.last_updated, today)
            if age is not None and age > STALE_PRICE_DAYS:
                flags.append(f"price is {age} days old - check it is still right")
            if base_qty is not None:
                cost = base_qty * item.price_per_base_unit

        subtotal += cost
        cost_lines.append(
            CostLine(item.name, scaled_qty, item.unit, base_qty, item.base_unit,
                     item.price_per_base_unit, cost, flags)
        )

    wastage_amount = subtotal * wastage_percent / 100.0
    return CostEstimate(cost_lines, subtotal, wastage_percent, wastage_amount, subtotal + wastage_amount)


def estimate_weight_kg(lines: list[CostLine], yield_unit: str, order_quantity: float, order_unit: str) -> float:
    """Approximate finished weight of an order, used for per-kg fixed costs.

    * Weight-based recipes: the order itself (e.g. 1 kg cake -> 1.0).
    * Volume-based: assume ~1 g per ml (close enough for batters and drinks).
    * Piece-based (cookies, brownies): add up the g/ml ingredients after
      scaling. Baking loses some moisture, so this slightly over-estimates;
      that errs on the side of not under-charging.
    """
    dim = units.dimension_of(yield_unit)
    if dim == units.MASS:
        return units.convert(order_quantity, order_unit, "kg")
    if dim == units.VOLUME:
        return units.convert(order_quantity, order_unit, "l")
    grams = sum(l.base_quantity or 0.0 for l in lines if l.base_unit in ("g", "ml"))
    return grams / 1000.0


# ---------------------------------------------------------------------------
# 3-5. Fixed costs, profit, tax, rounding
# ---------------------------------------------------------------------------

FIXED_COST_TYPES = ("per_order", "per_kg", "percent_of_cost")


def apply_fixed_costs(ingredient_cost: float, fixed_costs: list[FixedCost], weight_kg: float) -> list[tuple[str, float]]:
    """Turn each fixed-cost rule into a rupee amount for this order.

    per_order       flat amount (e.g. Rs 30 for a cake box)
    per_kg          amount x order weight (e.g. gas Rs 20 per kg baked)
    percent_of_cost % of ingredient cost (e.g. 10 % for spoilage/overheads)
    """
    out: list[tuple[str, float]] = []
    for fc in fixed_costs:
        if fc.type == "per_order":
            amount = fc.value
        elif fc.type == "per_kg":
            amount = fc.value * weight_kg
        elif fc.type == "percent_of_cost":
            amount = ingredient_cost * fc.value / 100.0
        else:
            raise ValueError(f"Unknown fixed cost type '{fc.type}'.")
        out.append((fc.name, amount))
    return out


def apply_profit(cost: float, percent: float, mode: str) -> float:
    """Selling price (before tax) for a given profit percentage.

    MARKUP: profit is a % of COST.   price = cost x (1 + m)
            Rs 100 cost at 50 % markup -> Rs 150 (profit Rs 50 = 33 % of price)
    MARGIN: profit is a % of PRICE.  price = cost / (1 - m)
            Rs 100 cost at 50 % margin -> Rs 200 (profit Rs 100 = 50 % of price)

    The same "50 %" gives very different prices, which is why the UI makes the
    baker choose explicitly. A margin of 100 % or more is impossible (it would
    need an infinite price), so we refuse it with a clear message.
    """
    m = percent / 100.0
    if m < 0:
        raise ValueError("Profit percentage cannot be negative.")
    if mode == "markup":
        return cost * (1 + m)
    if mode == "margin":
        if m >= 1:
            raise ValueError("A margin must be below 100 % (profit can't be the whole price).")
        return cost / (1 - m)
    raise ValueError(f"Unknown profit mode '{mode}' (use 'margin' or 'markup').")


def round_to_nearest(amount: float, step: float) -> float:
    """Round half-up to the nearest `step` (5 -> Rs 5, 10 -> Rs 10, 0/1 -> no rounding).

    Python's built-in round() uses banker's rounding (round(2.5) == 2), which
    surprises people with money, so we round half up explicitly.
    """
    if not step or step <= 0:
        return round(amount, 2)
    return math.floor(amount / step + 0.5) * step


def price_order(
    ingredient_cost: float,
    fixed_costs: list[FixedCost],
    weight_kg: float,
    profit_percent: float,
    profit_mode: str,
    tax_percent: float,
    rounding_step: float,
) -> PriceBreakdown:
    """Run the full pricing pipeline: fixed costs -> profit -> tax -> round off.

    Tax is charged on the selling price (that's how GST works: on the value
    of supply). Rounding happens last and is shown as its own "Round off"
    line, as on normal Indian invoices, so every number on the bill adds up.
    """
    if tax_percent < 0:
        raise ValueError("Tax percentage cannot be negative.")
    fixed_lines = apply_fixed_costs(ingredient_cost, fixed_costs, weight_kg)
    fixed_total = sum(a for _, a in fixed_lines)
    production_cost = ingredient_cost + fixed_total
    selling_price = apply_profit(production_cost, profit_percent, profit_mode)
    tax_amount = selling_price * tax_percent / 100.0
    before_round = selling_price + tax_amount
    final_total = round_to_nearest(before_round, rounding_step)
    return PriceBreakdown(
        ingredient_cost=ingredient_cost,
        fixed_cost_lines=fixed_lines,
        fixed_costs_total=fixed_total,
        production_cost=production_cost,
        profit_mode=profit_mode,
        profit_percent=profit_percent,
        profit_amount=selling_price - production_cost,
        selling_price=selling_price,
        tax_percent=tax_percent,
        tax_amount=tax_amount,
        total_before_rounding=before_round,
        round_off=final_total - before_round,
        final_total=final_total,
    )


# ---------------------------------------------------------------------------
# Whole orders (one or more items) - used by the New Order page and the tests
#
# Ingredients are costed per item. Fixed costs belong to the ORDER: a box and
# a delivery trip are paid once, however many items are in it, and per-kg
# costs use the total weight. To print a price on every invoice line, each
# fixed cost is then shared between the items:
#   per_kg          in proportion to each item's weight
#   per_order       in proportion to each item's ingredient cost
#   percent_of_cost in proportion to each item's ingredient cost
# Profit (margin or markup) is linear in cost, so pricing every line
# separately adds up to exactly the same total as pricing the whole order.
# Tax and round-off are worked out from the printed (paise-rounded) line
# amounts, so every number on the invoice adds up to the paisa.
# Cost: O(total ingredient rows + items x fixed costs).
# ---------------------------------------------------------------------------


@dataclass
class OrderItem:
    """One thing the customer orders: a recipe and how much of it."""

    recipe: dict          # from db.get_recipe()
    quantity: float
    unit: str


@dataclass
class QuotedItem:
    recipe_name: str
    order_quantity: float
    order_unit: str
    factor: float
    estimate: CostEstimate
    weight_kg: float
    fixed_share: float = 0.0      # this item's share of the order's fixed costs
    selling_price: float = 0.0    # before tax, exact
    amount: float = 0.0           # selling price rounded to paise, as printed on the invoice

    @property
    def order_text(self) -> str:
        return f"{self.order_quantity:g} {self.order_unit}"

    @property
    def production_cost(self) -> float:
        return self.estimate.total + self.fixed_share


@dataclass
class Quote:
    items: list[QuotedItem]
    price: PriceBreakdown         # the whole-order pipeline (fixed costs counted once)
    subtotal: float               # sum of the printed line amounts
    tax_amount: float
    round_off: float
    final_total: float

    @property
    def weight_kg(self) -> float:
        return sum(i.weight_kg for i in self.items)

    @property
    def warnings(self) -> list[str]:
        many = len(self.items) > 1
        return [f"{i.recipe_name}: {w}" if many else w for i in self.items for w in i.estimate.warnings]


def lines_from_recipe(recipe: dict) -> list[IngredientLine]:
    """Convert db.get_recipe() items into IngredientLine objects."""
    return [
        IngredientLine(
            name=it["ingredient_name"], quantity=it["quantity"], unit=it["unit"], base_unit=it["base_unit"],
            price_per_base_unit=it["current_price_per_base_unit"], last_updated=it["last_updated"],
        )
        for it in recipe["items"]
    ]


def _shares(values: list[float]) -> list[float]:
    """Each value's fraction of the total; equal shares if the total is 0."""
    total = sum(values)
    if total <= 0:
        return [1 / len(values)] * len(values)
    return [v / total for v in values]


def build_quote(items: list[OrderItem], fixed_costs: list[dict], profit_percent: float, profit_mode: str,
                tax_percent: float, rounding_step: float, today: date | None = None) -> Quote:
    """Price a whole order: scale and cost each item, add fixed costs once,
    then profit, tax and rounding (see the section comment above)."""
    if not items:
        raise ValueError("Add at least one item to the order.")

    quoted: list[QuotedItem] = []
    for item in items:
        r = item.recipe
        factor = scale_factor(r["yield_quantity"], r["yield_unit"], item.quantity, item.unit)
        estimate = estimate_cost(lines_from_recipe(r), factor, r["wastage_percent"], today)
        weight = estimate_weight_kg(estimate.lines, r["yield_unit"], item.quantity, item.unit)
        quoted.append(QuotedItem(r["name"], item.quantity, item.unit, factor, estimate, weight))

    rules = [FixedCost(fc["name"], fc["type"], fc["value"]) for fc in fixed_costs if fc.get("active", 1)]
    ingredient_cost = sum(q.estimate.total for q in quoted)
    price = price_order(ingredient_cost, rules, sum(q.weight_kg for q in quoted),
                        profit_percent, profit_mode, tax_percent, rounding_step)

    # Share each fixed cost between the items.
    by_cost = _shares([q.estimate.total for q in quoted])
    by_weight = _shares([q.weight_kg for q in quoted])
    for rule, (_, amount) in zip(rules, price.fixed_cost_lines):
        shares = by_weight if rule.type == "per_kg" else by_cost
        for q, share in zip(quoted, shares):
            q.fixed_share += amount * share

    for q in quoted:
        q.selling_price = apply_profit(q.production_cost, profit_percent, profit_mode)
        q.amount = round(q.selling_price, 2)

    subtotal = round(sum(q.amount for q in quoted), 2)
    tax_amount = round(subtotal * tax_percent / 100.0, 2)
    final_total = round_to_nearest(subtotal + tax_amount, rounding_step)
    round_off = round(final_total - subtotal - tax_amount, 2)
    return Quote(quoted, price, subtotal, tax_amount, round_off, final_total)


def invoice_line_items(quote: Quote) -> list[dict]:
    """What the customer sees: one line per item."""
    return [{"description": q.recipe_name, "quantity_text": q.order_text, "amount": q.amount} for q in quote.items]


def cost_sheet(quote: Quote) -> dict:
    """A JSON-friendly snapshot of every number, stored with the invoice so the
    internal cost sheet can be reprinted later even if prices change."""
    items = []
    for q in quote.items:
        lines = []
        for l in q.estimate.lines:
            shown_price, per = units.price_for_display(l.price_per_base_unit, l.base_unit)
            lines.append({
                "name": l.name, "cost": round(l.cost, 2), "flags": l.flags,
                "quantity_text": f"{l.quantity:g} {l.unit}",
                "price_text": "no price" if shown_price is None else f"Rs. {shown_price:,.2f}/{per}",
            })
        items.append({
            "recipe_name": q.recipe_name, "order_text": q.order_text, "scale_factor": q.factor,
            "weight_kg": q.weight_kg, "lines": lines,
            "ingredients_subtotal": q.estimate.ingredients_subtotal,
            "wastage_percent": q.estimate.wastage_percent, "wastage_amount": q.estimate.wastage_amount,
            "ingredient_cost": q.estimate.total, "fixed_share": q.fixed_share,
            "production_cost": q.production_cost, "amount": q.amount,
        })
    p = quote.price
    production_cost = sum(q.production_cost for q in quote.items)
    return {
        "items": items,
        "ingredient_cost": p.ingredient_cost, "fixed_cost_lines": p.fixed_cost_lines,
        "fixed_costs_total": p.fixed_costs_total, "production_cost": production_cost,
        "profit_mode": p.profit_mode, "profit_percent": p.profit_percent,
        "profit_amount": quote.subtotal - production_cost,
        "selling_price": quote.subtotal, "tax_percent": p.tax_percent, "tax_amount": quote.tax_amount,
        "round_off": quote.round_off, "final_total": quote.final_total, "warnings": quote.warnings,
    }
