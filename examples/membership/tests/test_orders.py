import numpy as np

from orders import Order, held_orders


def book(*customers: int) -> list[Order]:
    return [
        Order(order_id=100 + i, customer_id=c, amount_cents=500 + i)
        for i, c in enumerate(customers)
    ]


def ids(orders: list[Order]) -> list[int]:
    return [o.order_id for o in orders]


def test_keeps_blocked_orders_in_arrival_order() -> None:
    orders = book(7, 3, 9, 3, 5, 7)

    assert ids(held_orders(orders, [3, 7])) == [100, 101, 103, 105]


def test_keeps_every_order_of_a_blocked_customer() -> None:
    orders = book(4, 4, 4, 8)

    assert ids(held_orders(orders, [4, 2])) == [100, 101, 102]


def test_repeated_blocklist_entries_hold_an_order_once() -> None:
    orders = book(1, 2, 3)

    assert ids(held_orders(orders, [2, 3, 2, 3, 3])) == [101, 102]


def test_empty_blocklist_holds_nothing() -> None:
    assert held_orders(book(1, 2, 3), []) == []


def test_no_orders() -> None:
    assert held_orders([], [1, 2, 3]) == []


def test_blocked_ids_without_orders_are_ignored() -> None:
    orders = book(10, 20)

    assert ids(held_orders(orders, [30, 40, 20])) == [101]


def test_returns_the_same_order_objects() -> None:
    orders = book(5, 6)

    held = held_orders(orders, [6, 9])

    assert held[0] is orders[1]


def test_accepts_tuples_and_leaves_inputs_alone() -> None:
    orders = tuple(book(1, 2, 3, 2))
    blocklist = (2, 3)

    held = held_orders(orders, blocklist)

    assert ids(held) == [101, 102, 103]
    assert orders == tuple(book(1, 2, 3, 2))
    assert blocklist == (2, 3)


def test_matches_reference_on_random_books() -> None:
    rng = np.random.default_rng(7)
    for _ in range(20):
        customers = rng.integers(0, 50, 200).tolist()
        blocklist = rng.integers(0, 50, 12).tolist()
        orders = book(*customers)

        expected = [
            o for o in orders if any(o.customer_id == b for b in blocklist)
        ]

        assert held_orders(orders, blocklist) == expected
