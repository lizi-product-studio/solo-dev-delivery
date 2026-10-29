"""Synthetic buggy pricing helper to repair."""
def quote_total(unit_price, quantity, discount=None):
    discount = discount or 0
    return round(float(unit_price) * quantity * (1 - float(discount) / 100), 2)
