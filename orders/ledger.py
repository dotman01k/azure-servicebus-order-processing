"""Atomic inbox + order persistence; no remote payment or shipping side effects."""
import threading
from datetime import datetime, timezone
from .domain import IdempotencyConflict


def assert_same(entity, order):
    if entity['fingerprint'] != order.fingerprint:
        raise IdempotencyConflict('Identifier reused for a different order')


def entities(order):
    common = {'PartitionKey': order.partition, 'fingerprint': order.fingerprint,
              'received_at': datetime.now(timezone.utc).isoformat()}
    event = {**common, 'RowKey': order.event_key, 'kind': 'inbox', 'order_key': order.order_key}
    business = {**common, 'RowKey': order.order_key, 'kind': 'order',
                'order_id': order.order_id, 'amount_cents': order.amount_cents, 'currency': order.currency}
    return event, business


class MemoryLedger:
    """Thread-safe executable model for offline tests; not a distributed store."""
    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()

    def apply(self, order):
        event, business = entities(order)
        ek = (order.partition, order.event_key)
        ok = (order.partition, order.order_key)
        with self.lock:
            if ek in self.rows:
                assert_same(self.rows[ek], order)
                return 'duplicate_event'
            if ok in self.rows:
                assert_same(self.rows[ok], order)
                self.rows[ek] = event
                return 'duplicate_order'
            self.rows.update({ek: event, ok: business})
            return 'created'


class TableLedger:
    def __init__(self, table):
        self.table = table

    def _get(self, partition, row):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return self.table.get_entity(partition_key=partition, row_key=row)
        except ResourceNotFoundError:
            return None

    def apply(self, order):
        from azure.data.tables import TableTransactionError
        from azure.core.exceptions import ResourceExistsError, HttpResponseError
        event, business = entities(order)
        existing = self._get(order.partition, order.event_key)
        if existing:
            assert_same(existing, order)
            return 'duplicate_event'
        try:
            # Both creates are within the SAME tenant partition. Failure rolls both back.
            self.table.submit_transaction([('create', event), ('create', business)])
            return 'created'
        except (TableTransactionError, ResourceExistsError, HttpResponseError) as exc:
            if getattr(exc, 'status_code', None) != 409:
                raise
            # Another replica won either the event or the order create. Reconcile reads
            # under strong consistency; never turn a timeout or auth failure into success.
            prior_event = self._get(order.partition, order.event_key)
            if prior_event:
                assert_same(prior_event, order)
                return 'duplicate_event'
            prior_order = self._get(order.partition, order.order_key)
            if not prior_order:
                raise
            assert_same(prior_order, order)
            try:
                # The order already exists, but bind this new event_id as well.
                self.table.create_entity(event)
            except ResourceExistsError:
                concurrent = self._get(order.partition, order.event_key)
                if concurrent is None:
                    raise
                assert_same(concurrent, order)
            return 'duplicate_order'
