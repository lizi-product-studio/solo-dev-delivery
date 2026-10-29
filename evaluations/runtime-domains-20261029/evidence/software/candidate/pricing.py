"""Decimal pricing with explicit validation and one final rounding step."""

from decimal import (
    Context,
    Decimal,
    InvalidOperation,
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_UP,
    localcontext,
)


def _decimal_value(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal, str)):
        raise TypeError(f"{name} must be an int, Decimal, or decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must contain a valid decimal number") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def quote_total(unit_price, quantity, discount=None):
    price = _decimal_value(unit_price, "unit_price")
    if price < 0:
        raise ValueError("unit_price must be nonnegative")

    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise TypeError("quantity must be an int")
    if quantity < 0:
        raise ValueError("quantity must be nonnegative")

    reduction = Decimal(0) if discount is None else _decimal_value(discount, "discount")
    if not Decimal(0) <= reduction <= Decimal(100):
        raise ValueError("discount must be between 0 and 100")

    # Validate every argument before handling a zero total.
    if quantity == 0 or price.is_zero() or reduction == 100:
        return Decimal("0.00")
    if reduction.is_zero():
        reduction = Decimal(0)

    count = Decimal(quantity)
    price_digits = len(price.as_tuple().digits)
    quantity_digits = len(count.as_tuple().digits)
    # Allow exact subtraction in 1 - discount / 100, exact products,
    # and enough integral digits for the final quantization to cents.
    factor_digits = max(3, 3 - reduction.as_tuple().exponent)
    precision = max(
        28,
        price_digits + quantity_digits + factor_digits + 2,
        price.adjusted() + quantity_digits + 3,
    )
    context = Context(
        prec=precision,
        rounding=ROUND_HALF_UP,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
    )
    # A fresh context keeps caller precision and traps from affecting pricing.
    with localcontext(context):
        total = price * count * (Decimal(1) - reduction / Decimal(100))
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
