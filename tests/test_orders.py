import concurrent.futures
import json
import os
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError, HttpResponseError
from azure.servicebus import ServiceBusMessage
from orders.domain import Order, InvalidOrder, IdempotencyConflict
from orders.ledger import MemoryLedger, TableLedger
from orders.processing import process
from orders.replay import replay_one
from orders.worker import message_body
from orders.azure import configuration, credential


def order(**changes):return replace(Order(1,'evt-1','tenant-1','order-1',12500,'USD'),**changes)


class ContractTests(unittest.TestCase):
    def test_roundtrip(self):self.assertEqual(Order.parse(order().to_bytes()),order())
    def test_json_order_does_not_change_fingerprint(self):
        self.assertEqual(Order.parse(json.dumps(json.loads(order().to_bytes())).encode()).fingerprint,order().fingerprint)
    def test_event_is_not_business_fingerprint(self):self.assertEqual(order().fingerprint,order(event_id='evt-2').fingerprint)
    def test_amount_changes_fingerprint(self):self.assertNotEqual(order().fingerprint,order(amount_cents=1).fingerprint)
    def test_broker_message_ids_are_tenant_scoped(self):
        self.assertNotEqual(order().transport_id,order(tenant_id='tenant-2').transport_id)
        self.assertEqual(order().transport_id,order().transport_id)
        self.assertLessEqual(len(order().transport_id),128)
    def test_tenant_isolation(self):self.assertNotEqual(order().partition,order(tenant_id='tenant-2').partition)
    def test_invalid_identifiers(self):
        for v in ('','../bad','a/b','a'*65,'a b'):
            with self.subTest(v=v),self.assertRaises(InvalidOrder):Order.parse(order(order_id=v).to_bytes())
    def test_invalid_amounts(self):
        for v in (True,False,0,-1,1.5,'100',100000001):
            with self.subTest(v=v),self.assertRaises(InvalidOrder):Order.parse(order(amount_cents=v).to_bytes())
    def test_missing_extra_non_object(self):
        o=json.loads(order().to_bytes())
        for v in ({**o,'extra':1},{k:v for k,v in o.items() if k!='event_id'},[],None):
            with self.assertRaises(InvalidOrder):Order.parse(json.dumps(v).encode())
    def test_schema_and_currency(self):
        for k,v in [('schema_version',True),('schema_version',2),('currency','usd'),('currency','US')]:
            with self.assertRaises(InvalidOrder):Order.parse(order(**{k:v}).to_bytes())
    def test_duplicate_json_properties(self):
        with self.assertRaises(InvalidOrder):Order.parse(b'{"schema_version":1,"schema_version":2}')
    def test_bad_utf8_json_and_size(self):
        for v in (b'\xff',b'not-json',b' '*16385):
            with self.assertRaises(InvalidOrder):Order.parse(v)


class LedgerTests(unittest.TestCase):
    def test_atomic_new_order(self):
        l=MemoryLedger();self.assertEqual(l.apply(order()),'created');self.assertEqual(len(l.rows),2)
    def test_duplicate_event(self):
        l=MemoryLedger();l.apply(order());self.assertEqual(l.apply(order()),'duplicate_event');self.assertEqual(len(l.rows),2)
    def test_new_event_same_order_binds_event(self):
        l=MemoryLedger();l.apply(order());self.assertEqual(l.apply(order(event_id='evt-2')),'duplicate_order')
        self.assertEqual(len(l.rows),3)
        with self.assertRaises(IdempotencyConflict):l.apply(order(event_id='evt-2',order_id='other'))
    def test_event_conflict(self):
        l=MemoryLedger();l.apply(order())
        with self.assertRaises(IdempotencyConflict):l.apply(order(amount_cents=5))
        self.assertEqual(len(l.rows),2)
    def test_order_conflict(self):
        l=MemoryLedger();l.apply(order())
        with self.assertRaises(IdempotencyConflict):l.apply(order(event_id='evt-2',amount_cents=1))
        self.assertEqual(len(l.rows),2)
    def test_other_tenant(self):
        l=MemoryLedger();l.apply(order());l.apply(order(tenant_id='tenant-2'));self.assertEqual(len(l.rows),4)
    def test_concurrent_duplicates(self):
        l=MemoryLedger()
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:results=list(pool.map(lambda _:l.apply(order()),range(100)))
        self.assertEqual(results.count('created'),1);self.assertEqual(results.count('duplicate_event'),99);self.assertEqual(len(l.rows),2)


class FakeTable:
    """Atomic create-only fixture, not an Azure emulator."""
    def __init__(self):self.rows={};self.fail=None;self.before_batch=None
    def get_entity(self,partition_key,row_key):
        if (partition_key,row_key) not in self.rows:raise ResourceNotFoundError('Not found')
        return self.rows[partition_key,row_key]
    def create_entity(self,entity):
        key=entity['PartitionKey'],entity['RowKey']
        if key in self.rows:raise ResourceExistsError('Exists')
        self.rows[key]=dict(entity)
    def submit_transaction(self,ops):
        if self.fail:raise self.fail
        if self.before_batch:
            cb=self.before_batch;self.before_batch=None;cb()
        if any((e['PartitionKey'],e['RowKey']) in self.rows for _,e in ops):
            e=HttpResponseError('Conflict');e.status_code=409;raise e
        self.rows.update({(e['PartitionKey'],e['RowKey']):dict(e) for _,e in ops})


class TableAdapterTests(unittest.TestCase):
    def setUp(self):self.table=FakeTable();self.ledger=TableLedger(self.table)
    def test_create_and_duplicate(self):
        self.assertEqual(self.ledger.apply(order()),'created');self.assertEqual(self.ledger.apply(order()),'duplicate_event')
    def test_order_conflict_reconciled(self):
        self.ledger.apply(order());self.assertEqual(self.ledger.apply(order(event_id='evt-2')),'duplicate_order');self.assertEqual(len(self.table.rows),3)
    def test_no_orphan_inbox(self):
        self.ledger.apply(order())
        with self.assertRaises(IdempotencyConflict):self.ledger.apply(order(event_id='evt-2',amount_cents=5))
        self.assertEqual(len(self.table.rows),2)
    def test_timeout_propagates(self):
        self.table.fail=TimeoutError()
        with self.assertRaises(TimeoutError):self.ledger.apply(order())
        self.assertEqual(self.table.rows,{})
    def test_auth_failure_propagates(self):
        e=HttpResponseError('Forbidden');e.status_code=403;self.table.fail=e
        with self.assertRaises(HttpResponseError):self.ledger.apply(order())
    def test_ambiguous_commit_timeout_is_safe_on_redelivery(self):
        original=self.table.submit_transaction
        def commit_then_timeout(ops):
            original(ops)
            raise TimeoutError('Commit response lost')
        self.table.submit_transaction=commit_then_timeout
        with self.assertRaises(TimeoutError):self.ledger.apply(order())
        self.assertEqual(self.ledger.apply(order()),'duplicate_event')
        self.assertEqual(len(self.table.rows),2)
    def test_replica_wins_race(self):
        self.table.before_batch=lambda:TableLedger(self.table).apply(order())
        self.assertEqual(self.ledger.apply(order()),'duplicate_event');self.assertEqual(len(self.table.rows),2)


class SettlementTests(unittest.TestCase):
    def test_commit_precedes_ack(self):
        l=MemoryLedger();ack=Mock(side_effect=lambda:self.assertEqual(len(l.rows),2));dlq=Mock()
        self.assertEqual(process(order().to_bytes(),l,ack,dlq),'created');ack.assert_called_once();dlq.assert_not_called()
    def test_crash_after_commit_then_redelivery(self):
        l=MemoryLedger()
        with self.assertRaises(ConnectionError):process(order().to_bytes(),l,Mock(side_effect=ConnectionError()),Mock())
        self.assertEqual(process(order().to_bytes(),l,Mock(),Mock()),'duplicate_event');self.assertEqual(len(l.rows),2)
    def test_storage_failure_not_settled(self):
        l=Mock();l.apply.side_effect=TimeoutError();ack=Mock();dlq=Mock()
        with self.assertRaises(TimeoutError):process(order().to_bytes(),l,ack,dlq)
        ack.assert_not_called();dlq.assert_not_called()
    def test_invalid_deadlettered_without_storage(self):
        l=Mock();ack=Mock();dlq=Mock();self.assertEqual(process(b'{}',l,ack,dlq),'dead_letter_invalid')
        l.apply.assert_not_called();ack.assert_not_called();self.assertEqual(dlq.call_args.args[0],'InvalidOrder')
    def test_conflict_deadlettered(self):
        l=MemoryLedger();l.apply(order());ack=Mock()
        self.assertEqual(process(order(amount_cents=2).to_bytes(),l,ack,Mock()),'dead_letter_conflict');ack.assert_not_called()
    def test_failed_deadletter_not_success(self):
        with self.assertRaises(ConnectionError):process(b'{}',Mock(),Mock(),Mock(side_effect=ConnectionError()))


class ReplayTests(unittest.TestCase):
    def test_send_precedes_complete(self):
        calls=[];replay_one(order().to_bytes(),42,lambda *_:calls.append('send'),lambda:calls.append('complete'));self.assertEqual(calls,['send','complete'])
    def test_failed_send_keeps_dlq(self):
        done=Mock()
        with self.assertRaises(ConnectionError):replay_one(order().to_bytes(),42,Mock(side_effect=ConnectionError()),done)
        done.assert_not_called()
    def test_invalid_does_not_send(self):
        send=Mock();done=Mock()
        with self.assertRaises(InvalidOrder):replay_one(b'{}',42,send,done)
        send.assert_not_called();done.assert_not_called()
    def test_stable_id_and_corrected_payload(self):
        a=replay_one(order().to_bytes(),42,Mock(),Mock());b=replay_one(order().to_bytes(),42,Mock(),Mock())
        c=replay_one(b'bad',42,Mock(),Mock(),replacement=order(amount_cents=2).to_bytes())
        self.assertEqual(a,b);self.assertNotEqual(a,c)
    def test_complete_failure_then_resend_stable(self):
        sent=[]
        with self.assertRaises(ConnectionError):replay_one(order().to_bytes(),42,lambda _,mid:sent.append(mid),Mock(side_effect=ConnectionError()))
        replay_one(order().to_bytes(),42,lambda _,mid:sent.append(mid),Mock());self.assertEqual(sent[0],sent[1])


class WiringTests(unittest.TestCase):
    def test_real_sdk_wire_body(self):self.assertEqual(message_body(ServiceBusMessage(order().to_bytes())),order().to_bytes())
    def test_bad_wire_body(self):
        self.assertEqual(message_body(Mock(body=['text'])),b'');self.assertEqual(message_body(Mock(body=[b'x'*16385])),b'')
    def test_rejects_arbitrary_endpoint(self):
        with patch.dict(os.environ,{'SERVICEBUS_NAMESPACE':'attacker.example','TABLE_ENDPOINT':'http://example.com'},clear=True):
            with self.assertRaises(ValueError):configuration()
    def test_accepts_project_endpoint(self):
        with patch.dict(os.environ,{'SERVICEBUS_NAMESPACE':'demo.servicebus.windows.net','TABLE_ENDPOINT':'https://demostore.table.core.windows.net/'},clear=True):self.assertEqual(configuration()[2:],('orders','OrderLedger'))
    def test_credential_has_no_secret_fallback(self):
        with patch.dict(os.environ,{},clear=True),patch('orders.azure.AzureCliCredential') as cli:credential();cli.assert_called_once()
        with patch.dict(os.environ,{'AZURE_CLIENT_ID':'example'},clear=True),patch('orders.azure.ManagedIdentityCredential') as mi:credential();mi.assert_called_once_with(client_id='example')


if __name__=='__main__':unittest.main()
