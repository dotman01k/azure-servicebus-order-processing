import argparse
import json
from pathlib import Path
from .domain import Order


def main():
    p = argparse.ArgumentParser(description='Order producer, ledger reader and bounded DLQ replay')
    sub = p.add_subparsers(dest='command', required=True)
    send = sub.add_parser('send'); send.add_argument('file', type=Path)
    sub.add_parser('list-orders')
    replay = sub.add_parser('replay')
    replay.add_argument('--limit', type=int, default=10)
    replay.add_argument('--apply', action='store_true')
    replay.add_argument('--replacement', type=Path)
    args = p.parse_args()
    if args.command == 'replay' and (not 1 <= args.limit <= 100 or (args.replacement and args.limit != 1)):
        p.error('limit must be 1..100; replacement requires limit=1')
    # Validate local input before requesting any cloud credential.
    payload = Order.parse(args.file.read_bytes()) if args.command == 'send' else None
    replacement = args.replacement.read_bytes() if args.command == 'replay' and args.replacement else None
    if replacement is not None:
        Order.parse(replacement)
    from azure.servicebus import ServiceBusMessage, ServiceBusSubQueue, AutoLockRenewer
    from .azure import credential, clients
    from .worker import message_body
    from .replay import replay_one
    with credential() as cred:
        bus, table, queue = clients(cred)
        with bus, table:
            if args.command == 'send':
                with bus.get_queue_sender(queue_name=queue) as sender:
                    sender.send_messages(ServiceBusMessage(payload.to_bytes(), message_id=payload.transport_id,
                                                            content_type='application/json'))
                print('Message accepted by Service Bus; verify ledger processing separately.')
            elif args.command == 'list-orders':
                # Query only one bounded page; avoid accidentally exporting an entire dataset.
                pages = table.query_entities("kind eq 'order'", results_per_page=100).by_page()
                first = next(pages, [])
                print(json.dumps([{k:v for k,v in e.items() if k not in ('PartitionKey','RowKey')} for e in first], default=str, indent=2))
                print('First page only (at most 100 orders).')
            elif not args.apply:
                with bus.get_queue_receiver(queue_name=queue, sub_queue=ServiceBusSubQueue.DEAD_LETTER) as receiver:
                    for m in receiver.peek_messages(max_message_count=args.limit):
                        print(json.dumps({'sequence':m.sequence_number, 'reason':m.dead_letter_reason,
                                          'message_id':str(m.message_id)}))
                print('Preview only. Use --apply to replay and settle this bounded batch.')
            else:
                with AutoLockRenewer(max_lock_renewal_duration=180) as renewer:
                    with bus.get_queue_receiver(queue_name=queue, sub_queue=ServiceBusSubQueue.DEAD_LETTER,
                            prefetch_count=0, auto_lock_renewer=renewer) as receiver, bus.get_queue_sender(queue_name=queue) as sender:
                        # Receive one at a time so a large replay batch cannot expire idle locks.
                        for _ in range(args.limit):
                            batch = receiver.receive_messages(max_message_count=1, max_wait_time=3)
                            if not batch:
                                break
                            m = batch[0]
                            try:
                                rid = replay_one(message_body(m), m.sequence_number,
                                    lambda data, mid: sender.send_messages(ServiceBusMessage(data,
                                        message_id=mid, content_type='application/json')),
                                    lambda: receiver.complete_message(m), replacement=replacement)
                                print(json.dumps({'replayed_sequence':m.sequence_number,'replay_id':rid}))
                            except Exception:
                                receiver.abandon_message(m)
                                raise


if __name__ == '__main__':
    main()
