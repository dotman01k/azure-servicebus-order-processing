"""Settlement order is deliberately persistence first, acknowledgement second."""
from .domain import Order, InvalidOrder, IdempotencyConflict


def process(body, ledger, acknowledge, dead_letter):
    try:
        order = Order.parse(body)
        outcome = ledger.apply(order)
    except InvalidOrder:
        dead_letter('InvalidOrder', 'Message failed schema validation')
        return 'dead_letter_invalid'
    except IdempotencyConflict:
        dead_letter('IdempotencyConflict', 'Event or order key has conflicting content')
        return 'dead_letter_conflict'
    # Storage/transport failures propagate. The broker can redeliver safely.
    acknowledge()
    return outcome
