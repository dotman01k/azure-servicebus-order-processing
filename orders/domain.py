"""Strict contract and deterministic keys, independent of cloud SDKs."""
import hashlib
import json
import re
from dataclasses import asdict, dataclass


class InvalidOrder(ValueError):
    pass


class IdempotencyConflict(ValueError):
    pass


@dataclass(frozen=True)
class Order:
    schema_version: int
    event_id: str
    tenant_id: str
    order_id: str
    amount_cents: int
    currency: str

    @classmethod
    def parse(cls, body):
        if not isinstance(body, bytes) or len(body) > 16384:
            raise InvalidOrder('Body must be UTF-8 JSON under 16 KiB')
        try:
            # Reject duplicate JSON keys instead of silently accepting the final value.
            def pairs(items):
                out = {}
                for k, v in items:
                    if k in out:
                        raise InvalidOrder('Duplicate JSON property')
                    out[k] = v
                return out
            obj = json.loads(body.decode('utf-8'), object_pairs_hook=pairs)
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise InvalidOrder('Invalid UTF-8 JSON') from exc
        fields = {'schema_version', 'event_id', 'tenant_id', 'order_id', 'amount_cents', 'currency'}
        if not isinstance(obj, dict) or set(obj) != fields:
            raise InvalidOrder('Unexpected or missing properties')
        if type(obj['schema_version']) is not int or obj['schema_version'] != 1:
            raise InvalidOrder('Unsupported schema version')
        for key in ('event_id', 'tenant_id', 'order_id'):
            if not isinstance(obj[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', obj[key]):
                raise InvalidOrder('Invalid identifier')
        if type(obj['amount_cents']) is not int or not 1 <= obj['amount_cents'] <= 100000000:
            raise InvalidOrder('Amount must be integer cents between 1 and 100000000')
        if not isinstance(obj['currency'], str) or not re.fullmatch('[A-Z]{3}', obj['currency']):
            raise InvalidOrder('Currency must be three uppercase letters')
        return cls(**obj)

    def to_bytes(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(',', ':')).encode()

    @property
    def transport_id(self):
        # Broker duplicate detection is queue-wide, so include the tenant scope.
        return 'event-' + hashlib.sha256((self.tenant_id + '|' + self.event_id).encode()).hexdigest()

    @property
    def fingerprint(self):
        # event_id is a transport/idempotency key, not a business order attribute.
        values = asdict(self)
        del values['event_id']
        return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    @property
    def partition(self):
        return 'tenant-' + hashlib.sha256(self.tenant_id.encode()).hexdigest()

    @property
    def event_key(self):
        return 'event-' + hashlib.sha256(self.event_id.encode()).hexdigest()

    @property
    def order_key(self):
        return 'order-' + hashlib.sha256(self.order_id.encode()).hexdigest()
