"""
Unit normalisation and conversion for BakeCost.

Every ingredient in the library is stored in ONE of three *base units*:

    g   - grams       (anything you weigh: flour, sugar, butter ...)
    ml  - millilitres (anything you pour: milk, oil, cream ...)
    pcs - pieces      (anything you count: eggs, cake boards ...)

Recipes and bills, however, use whatever unit the person wrote: "2 cups",
"1 kg", "1/2 tsp", "1 dozen". This module turns any of those into the base unit
so that costing is always a simple multiplication.

How conversion works (the algorithm)
------------------------------------
1. Normalise the unit text ("Kgs", "kilo", "KG." -> "kg") with a lookup table.
2. Look up the unit's *dimension* (mass, volume or count) and its factor to the
   base unit of that dimension (1 kg = 1000 g, 1 cup = 240 ml, 1 dozen = 12 pcs).
3. If source and target share a dimension: value * factor_from / factor_to.
4. If one is mass and the other volume, we need the ingredient's density
   (grams per millilitre). Kitchen measures like "1 cup flour" are volumes,
   but flour is bought by weight, so this bridge is essential for home bakers.
5. If one side is a count (pcs) and the other a mass, we need a typical piece
   weight (e.g. 1 egg ~ 50 g).
6. Anything else is impossible and raises UnitConversionError with a message a
   non-technical user can act on.

Every step is a dictionary lookup, so a conversion is O(1). Finding the density
for an ingredient name is O(D) where D is the size of the density table (~40).
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnitConversionError(ValueError):
    """Raised when a quantity cannot be converted between two units.

    It subclasses ValueError so callers can catch it either specifically or
    together with other "bad input" errors.
    """


# ---------------------------------------------------------------------------
# Unit tables
# ---------------------------------------------------------------------------

MASS, VOLUME, COUNT = "mass", "volume", "count"

BASE_UNITS = ("g", "ml", "pcs")
BASE_UNIT_OF_DIMENSION = {MASS: "g", VOLUME: "ml", COUNT: "pcs"}

# canonical unit -> (dimension, how many base units one of it equals)
# Spoon and cup sizes use the metric kitchen convention that Indian recipe
# books and measuring sets follow: tsp = 5 ml, tbsp = 15 ml, cup = 240 ml.
UNITS: dict[str, tuple[str, float]] = {
    "mg": (MASS, 0.001),
    "g": (MASS, 1.0),
    "kg": (MASS, 1000.0),
    "oz": (MASS, 28.3495),
    "lb": (MASS, 453.592),
    "ml": (VOLUME, 1.0),
    "l": (VOLUME, 1000.0),
    "tsp": (VOLUME, 5.0),
    "tbsp": (VOLUME, 15.0),
    "cup": (VOLUME, 240.0),
    "pcs": (COUNT, 1.0),
    "dozen": (COUNT, 12.0),
}

# Everything people actually type (or OCR produces) -> canonical unit.
UNIT_ALIASES: dict[str, str] = {
    # mass
    "mg": "mg", "milligram": "mg", "milligrams": "mg",
    "g": "g", "gm": "g", "gms": "g", "gr": "g", "grm": "g", "grms": "g",
    "gram": "g", "grams": "g", "gramme": "g", "grammes": "g",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg",
    "kilogram": "kg", "kilograms": "kg",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    # volume
    "ml": "ml", "mls": "ml", "millilitre": "ml", "millilitres": "ml",
    "milliliter": "ml", "milliliters": "ml",
    "l": "l", "lt": "l", "ltr": "l", "ltrs": "l", "litre": "l", "litres": "l",
    "liter": "l", "liters": "l",
    "tsp": "tsp", "tsps": "tsp", "teaspoon": "tsp", "teaspoons": "tsp",
    "tbsp": "tbsp", "tbsps": "tbsp", "tbs": "tbsp", "tbl": "tbsp",
    "tablespoon": "tbsp", "tablespoons": "tbsp",
    "cup": "cup", "cups": "cup", "c": "cup",
    # count
    "pcs": "pcs", "pc": "pcs", "piece": "pcs", "pieces": "pcs",
    "nos": "pcs", "no": "pcs", "unit": "pcs", "units": "pcs", "ea": "pcs",
    "dozen": "dozen", "dozens": "dozen", "doz": "dozen", "dz": "dozen",
}

# ---------------------------------------------------------------------------
# Density table (grams per millilitre) for common baking ingredients.
#
# Source of the numbers: the standard "1 cup = X grams" charts used by baking
# books (King Arthur / Indian baking blogs), divided by 240 ml per cup.
#   flour/maida  1 cup ~ 125 g -> 0.52     sugar (granulated) 1 cup ~ 200 g -> 0.83
#   icing sugar  1 cup ~ 120 g -> 0.50     brown sugar (packed) 1 cup ~ 220 g -> 0.92
#   butter       1 cup ~ 227 g -> 0.95     cocoa powder 1 cup ~ 85 g -> 0.35
#   milk         1 cup ~ 245 g -> 1.03     oil 1 cup ~ 218 g -> 0.91
# Spooned dry powders vary by +/-10 % depending on how they are scooped, which
# is listed as a known limitation in the README.
#
# Keys are matched against the ingredient name as whole words, longest key
# first, so "icing sugar" wins over "sugar" and "milk powder" over "milk".
# ---------------------------------------------------------------------------
DENSITY_G_PER_ML: dict[str, float] = {
    "all purpose flour": 0.52, "flour": 0.52, "maida": 0.52, "atta": 0.50,
    "whole wheat flour": 0.50, "cake flour": 0.48, "cornflour": 0.53,
    "corn flour": 0.53, "cornstarch": 0.53, "almond flour": 0.40,
    "sugar": 0.83, "caster sugar": 0.83, "brown sugar": 0.92,
    "icing sugar": 0.50, "powdered sugar": 0.50,
    "butter": 0.95, "ghee": 0.91, "oil": 0.91, "vegetable oil": 0.91,
    "cocoa": 0.35, "cocoa powder": 0.35, "coffee": 0.40,
    "milk": 1.03, "buttermilk": 1.03, "curd": 1.03, "yogurt": 1.03,
    "dahi": 1.03, "cream": 1.00, "fresh cream": 1.00, "whipping cream": 1.00,
    "condensed milk": 1.30, "milk powder": 0.50, "water": 1.00,
    "honey": 1.42, "golden syrup": 1.40,
    "salt": 1.20, "baking powder": 0.96, "baking soda": 1.20,
    "vanilla": 1.00, "vanilla essence": 1.00, "essence": 1.00,
    "oats": 0.38, "rava": 0.70, "semolina": 0.70, "sooji": 0.70, "suji": 0.70,
    "chocolate chips": 0.70, "choco chips": 0.70, "walnuts": 0.50,
    "almonds": 0.60, "cashew": 0.55, "raisins": 0.62, "tutti frutti": 0.70,
    "cinnamon": 0.55, "yeast": 0.60, "gelatin": 0.70,
}

# Typical weight of one piece, for recipes that say "2 eggs" while the library
# stores eggs by weight (or the reverse). Medium Indian egg ~ 50 g.
PIECE_WEIGHT_G: dict[str, float] = {
    "egg": 50.0, "eggs": 50.0, "banana": 120.0, "lemon": 60.0,
}

# Pre-sorted once at import time so lookups try the most specific key first.
_DENSITY_KEYS = sorted(DENSITY_G_PER_ML, key=len, reverse=True)
_PIECE_KEYS = sorted(PIECE_WEIGHT_G, key=len, reverse=True)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def normalize_unit(unit: str | None) -> str:
    """Turn free text like 'Kgs.' or 'Tablespoons' into a canonical unit.

    Raises UnitConversionError for empty or unknown units, because silently
    guessing a unit would produce a wrong price without anyone noticing.
    """
    if unit is None:
        raise UnitConversionError("No unit given.")
    cleaned = re.sub(r"[^a-z]", "", str(unit).strip().lower())
    if cleaned in UNIT_ALIASES:
        return UNIT_ALIASES[cleaned]
    raise UnitConversionError(
        f"Unknown unit '{unit}'. Use one of: g, kg, ml, L, tsp, tbsp, cup, pcs, dozen."
    )


def is_known_unit(unit: str | None) -> bool:
    """True if `unit` can be normalised (handy for parsers deciding what a word is)."""
    try:
        normalize_unit(unit)
        return True
    except UnitConversionError:
        return False


def dimension_of(unit: str) -> str:
    """Return 'mass', 'volume' or 'count' for any accepted unit spelling."""
    return UNITS[normalize_unit(unit)][0]


def base_unit_for(unit: str) -> str:
    """The base unit (g / ml / pcs) that `unit` naturally converts into."""
    return BASE_UNIT_OF_DIMENSION[dimension_of(unit)]


def _lookup_by_name(name: str | None, keys: list[str], table: dict[str, float]) -> float | None:
    """Find the most specific table key that appears as whole words in `name`."""
    if not name:
        return None
    text = " " + re.sub(r"[^a-z ]", " ", name.lower()) + " "
    text = re.sub(r"\s+", " ", text)
    for key in keys:  # longest first => most specific match wins
        if f" {key} " in text:
            return table[key]
    return None


def density_for(ingredient_name: str | None) -> float | None:
    """Grams per millilitre for an ingredient, or None if we don't know it."""
    return _lookup_by_name(ingredient_name, _DENSITY_KEYS, DENSITY_G_PER_ML)


def piece_weight_for(ingredient_name: str | None) -> float | None:
    """Typical grams per piece for an ingredient, or None if we don't know it."""
    return _lookup_by_name(ingredient_name, _PIECE_KEYS, PIECE_WEIGHT_G)


def convert(value: float, from_unit: str, to_unit: str, ingredient_name: str | None = None) -> float:
    """Convert `value` from one unit to another.

    Examples
    --------
    >>> convert(1.5, "kg", "g")
    1500.0
    >>> convert(1, "cup", "g", "maida")      # needs the density table
    124.8
    >>> convert(2, "dozen", "pcs")
    24.0

    `ingredient_name` is only needed when crossing between mass and volume
    (density) or between count and mass (piece weight).
    """
    if value is None:
        raise UnitConversionError("No quantity given.")
    value = float(value)
    if value < 0:
        raise UnitConversionError("Quantity cannot be negative.")

    src, dst = normalize_unit(from_unit), normalize_unit(to_unit)
    src_dim, src_factor = UNITS[src]
    dst_dim, dst_factor = UNITS[dst]

    # Same dimension: a plain ratio of factors.
    if src_dim == dst_dim:
        return value * src_factor / dst_factor

    # Step 1: express the value in its own base unit (g, ml or pcs).
    amount = value * src_factor

    # Step 2: bridge to grams, which is the common ground for every bridge.
    if src_dim == VOLUME:
        amount *= _require_density(ingredient_name, src, dst)
    elif src_dim == COUNT:
        amount *= _require_piece_weight(ingredient_name, src, dst)
    # (src_dim == MASS: already in grams)

    # Step 3: from grams into the target dimension's base unit.
    if dst_dim == VOLUME:
        amount /= _require_density(ingredient_name, src, dst)
    elif dst_dim == COUNT:
        amount /= _require_piece_weight(ingredient_name, src, dst)

    return amount / dst_factor


def to_base(value: float, unit: str, base_unit: str, ingredient_name: str | None = None) -> float:
    """Convenience wrapper: convert into an ingredient's stored base unit."""
    if base_unit not in BASE_UNITS:
        raise UnitConversionError(f"'{base_unit}' is not a base unit (use g, ml or pcs).")
    return convert(value, unit, base_unit, ingredient_name)


def _require_density(name: str | None, src: str, dst: str) -> float:
    density = density_for(name)
    if density is None:
        raise UnitConversionError(
            f"Can't convert {src} to {dst} for '{name or 'this ingredient'}': "
            "its density (grams per cup) is unknown. Enter the quantity in "
            f"{BASE_UNIT_OF_DIMENSION[UNITS[dst][0]]} instead."
        )
    return density


def _require_piece_weight(name: str | None, src: str, dst: str) -> float:
    weight = piece_weight_for(name)
    if weight is None:
        raise UnitConversionError(
            f"Can't convert {src} to {dst} for '{name or 'this ingredient'}': "
            "we don't know how much one piece weighs. Enter the quantity in "
            f"{BASE_UNIT_OF_DIMENSION[UNITS[dst][0]]} instead."
        )
    return weight


# ---------------------------------------------------------------------------
# Friendly display units: people think in "Rs per kg", not "Rs per gram".
# ---------------------------------------------------------------------------
DISPLAY_UNIT = {"g": ("kg", 1000.0), "ml": ("L", 1000.0), "pcs": ("pc", 1.0)}


def price_for_display(price_per_base: float | None, base_unit: str) -> tuple[float | None, str]:
    """Convert a stored price per g/ml/pc into a price per kg/L/pc for the UI."""
    unit, factor = DISPLAY_UNIT[base_unit]
    return (None if price_per_base is None else price_per_base * factor), unit


def price_from_display(price_per_display: float, base_unit: str) -> float:
    """Inverse of price_for_display: Rs per kg -> Rs per g, etc."""
    return price_per_display / DISPLAY_UNIT[base_unit][1]
