"""Synthetic pricing helper with validated decimal arithmetic."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext


def _as_decimal(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal, str)):
        raise TypeError(f"{name} must be an int, Decimal, or decimal string")
    try:
        number = Decimal(value) if isinstance(value, (int, str)) else value
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be valid decimal text") from None
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def quote_total(unit_price, quantity, discount=None):
    """Return the discounted total rounded to cents using half-up rounding."""
    price = _as_decimal(unit_price, "unit_price")
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise TypeError("quantity must be an int")
    if quantity < 0:
        raise ValueError("quantity must be non-negative")
    rate = Decimal(0) if discount is None else _as_decimal(discount, "discount")

    if price < 0:
        raise ValueError("unit_price must be non-negative")
    if rate < 0 or rate > 100:
        raise ValueError("discount must be between 0 and 100")

    # Size the context to keep the multiplication and percentage calculation
    # exact, including values with widely separated decimal places.
    price_tuple = price.as_tuple()
    rate_tuple = rate.as_tuple()
    quantity_digits = len(str(quantity)) if quantity else 1
    precision = max(
        28,
        len(price_tuple.digits) + len(rate_tuple.digits) + quantity_digits
        + abs(price_tuple.exponent) + abs(rate_tuple.exponent) + 8,
    )
    with localcontext() as context:
        context.prec = precision
        total = price * quantity * (Decimal(1) - rate / Decimal(100))
        # Quantize needs room for the integer portion as well as cents.
        context.prec = max(context.prec, len(total.as_tuple().digits) + 2)
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
