"""Single-message receivers with lock renewal, structured logs and graceful drain."""
import json
import signal
import threading
import time
from datetime import datetime, timezone
from azure.servicebus import AutoLockRenewer
from azure.servicebus.exceptions import ServiceBusError, MessageLockLostError
from .azure import credential, clients
from .ledger import TableLedger
from .processing import process


def emit(event, **fields):
    print(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(), 'event': event, **fields}), flush=True)


def message_body(message):
    # Reject bodies not carried as AMQP DATA; producer emits UTF-8 bytes.
    try:
        chunks = []
        size = 0
        for chunk in message.body:
            if not isinstance(chunk, (bytes, bytearray)):
                raise ValueError('Unsupported AMQP body type')
            size += len(chunk)
            if size > 16384:
                raise ValueError('Oversized message')
            chunks.append(bytes(chunk))
        return b''.join(chunks)
    except (TypeError, ValueError):
        return b''  # Normal validation dead-letters malformed/unsupported bodies.


def main():
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with credential() as cred:
        bus, table, queue = clients(cred)
        with bus, table, AutoLockRenewer(max_lock_renewal_duration=180) as renewer:
            ledger = TableLedger(table)
            with bus.get_queue_receiver(queue_name=queue, prefetch_count=0,
                                        max_wait_time=5, auto_lock_renewer=renewer) as receiver:
                emit('worker_started')
                failures = 0
                while not stop.is_set():
                    messages = receiver.receive_messages(max_message_count=1, max_wait_time=5)
                    for message in messages:
                        started = time.monotonic()
                        try:
                            outcome = process(message_body(message), ledger,
                                lambda: receiver.complete_message(message),
                                lambda reason, desc: receiver.dead_letter_message(message,
                                    reason=reason, error_description=desc))
                            failures = 0
                            emit('message_processed', outcome=outcome,
                                 duration_ms=round((time.monotonic()-started)*1000))
                        except MessageLockLostError:
                            emit('settlement_lock_lost')
                        except Exception as exc:
                            emit('processing_failed', error_type=type(exc).__name__)
                            try:
                                receiver.abandon_message(message)
                            except ServiceBusError:
                                emit('abandon_failed')
                            failures += 1
                            if failures >= 5:
                                # Fail visibly rather than endlessly running with broken RBAC/storage.
                                raise RuntimeError('Repeated processing failures; inspect storage and identity') from exc
                            stop.wait(min(2 ** failures, 30))
                emit('worker_drained')


if __name__ == '__main__':
    main()
