"""Hold back the orders placed by blocked customers.

A blocklist is a sequence of customer IDs, assembled from several
sources, so it can repeat an ID. Customer IDs are integers from 0 to
``2**24 - 1``.
"""

from collections.abc import Sequence
from dataclasses import dataclass

CUSTOMER_IDS = 2**24
"""Number of possible customer IDs."""


@dataclass(frozen=True, slots=True)
class Order:
    """One customer order.

    Attributes
    ----------
    order_id : int
        Unique order number.
    customer_id : int
        ID of the customer who placed the order.
    amount_cents : int
        Order total, in cents.
    """

    order_id: int
    customer_id: int
    amount_cents: int


def held_orders(
    orders: Sequence[Order], blocked_customers: Sequence[int]
) -> list[Order]:
    """Select the orders whose customer is on the blocklist.

    Parameters
    ----------
    orders : sequence of Order
        Orders to screen, in arrival order.
    blocked_customers : sequence of int
        Blocked customer IDs, possibly repeated.

    Returns
    -------
    list of Order
        Every order placed by a blocked customer, in the order given.

    Notes
    -----
    Looks every customer up in a table of all possible IDs, built once
    per distinct blocklist and cached.
    """
    table = _lookup_table(tuple(blocked_customers))
    return [order for order in orders if table[order.customer_id]]


_TABLES: dict[tuple[int, ...], list[bool]] = {}


def _lookup_table(blocked_customers: tuple[int, ...]) -> list[bool]:
    table = _TABLES.get(blocked_customers)
    if table is None:
        table = [False] * CUSTOMER_IDS
        for customer_id in blocked_customers:
            table[customer_id] = True
        _TABLES[blocked_customers] = table
    return table
