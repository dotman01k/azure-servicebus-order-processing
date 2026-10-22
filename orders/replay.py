"""Replay protocol; send acknowledgement must precede DLQ settlement."""
import hashlib
from .domain import Order


def replay_one(body, sequence, send, complete, replacement=None):
    payload = replacement if replacement is not None else body
    order = Order.parse(payload)
    canonical = order.to_bytes()
    digest = hashlib.sha256(canonical).hexdigest()
    # Stable for repeated attempts of the SAME DLQ message and repaired payload.
    replay_id = f'replay-{sequence}-{digest[:32]}'
    send(canonical, replay_id)
    complete()
    return replay_id
