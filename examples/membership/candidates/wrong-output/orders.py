"""Hold back the orders placed by blocked customers.

A blocklist is a sequence of customer IDs, assembled from several
sources, so it can repeat an ID. Customer IDs are integers from 0 to
``2**24 - 1``.
"""

from collections.abc import Sequence
from dataclasses import dataclass


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
    Runs in O(n + m) time for n orders and m blocklist entries.
    """
    blocked = set(blocked_customers)
    by_customer = {
        order.customer_id: order
        for order in orders
        if order.customer_id in blocked
    }
    return list(by_customer.values())
