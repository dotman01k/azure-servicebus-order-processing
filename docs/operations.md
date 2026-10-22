# Operations and recovery

## Observe before changing anything

Check the active revision and image, system logs, queue count and application JSON events.
The worker logs error class names and result categories. It does not log raw order JSON.
The Log Analytics queries in queries.kql summarize processing outcomes and duration. The
foundation's dead-letter alert fires in Azure Monitor without an action group; add notification
routing explicitly if you need a page or email. A normal send response means broker acceptance,
not a completed order.

```bash
az containerapp revision list -g "$RESOURCE_GROUP" -n "$PREFIX-worker" -o table
az containerapp logs show -g "$RESOURCE_GROUP" -n "$PREFIX-worker" --type system --tail 50
# Console logs may require selecting an active replica/revision when scaling from zero.
az servicebus queue show -g "$RESOURCE_GROUP" --namespace-name "${SERVICEBUS_NAMESPACE%%.*}" \
  --name orders --query '{Active:countDetails.activeMessageCount,Dead:countDetails.deadLetterMessageCount}'
```

Service Bus Standard retains a base charge while the queue is empty. Avoid leaving a sandbox
running solely because Container Apps replicas have reached zero.

## Dead-letter preview and bounded replay

Use the optional operator identity with queue receiver/sender and table reader grants. Configure
the environment variables from the README and unset AZURE_CLIENT_ID for Azure CLI local auth.

```bash
python -m orders replay --limit 10
```

Preview peeks at metadata only. It does not lock, resend or settle messages. Confirm the cause
before applying. Repair storage availability, a lost role assignment or a broken revision first.

```bash
python -m orders replay --limit 10 --apply
```

The command receives and renews one DLQ message at a time, validates it, sends it to the primary
queue, waits for send acceptance, and only then completes the original DLQ message. Failures
retain the source; a send failure never completes it. This is not a cross-entity Service Bus
transaction. If the send succeeds but completion fails, the replay ID remains deterministic
for that sequence and payload; broker deduplication helps inside its ten-minute window, and
application-level inbox/order protection remains after that window.

Invalid messages are not automatically rewritten or skipped. If the first message needs an
approved replacement, prepare a corrected schema-valid JSON file and use a single-message batch:

```bash
python -m orders replay --limit 1 --replacement examples/order.json --apply
```

The replacement applies to the first available DLQ message. Verify the preview's message ID
and sequence immediately beforehand, and prevent concurrent recovery operators. This CLI does
not offer an exclusive operator lease or exact-sequence selection; for a busy shared DLQ,
export/inspect with a dedicated tool instead of assuming preview ordering is stable. The
replacement must respect existing event/order fingerprints. A different payload using a
committed identifier will be dead-lettered again. Preserve original invalid messages securely
before replacement if required for audit; completing the source removes it from the broker.

## Crash and transient-error behavior

If the worker crashes before the Table transaction commits, the lock eventually expires and
the message can be processed again. If the transaction committed but its response or broker
completion was lost, redelivery reads the inbox and does not create another order. The Table
adapter propagates authentication errors, timeouts and non-conflict HTTP failures instead of
pretending they are duplicate success.

On processing exceptions the worker attempts abandon and backs off. Five consecutive failures
terminate it visibly, allowing Container Apps restart behavior and alerts to show the incident.
A broken dependency can exhaust the queue's ten-delivery limit and move messages into the DLQ;
repair the dependency and replay, rather than raising maxDeliveryCount indefinitely. Lock renewal
is bounded to three minutes. Shutdown stops receiving and finishes the current short operation
where possible; it does not guarantee completion before platform termination.

## Revision rollback

Keep the previous image digest in the release record before deploying a new one. Re-deploy
worker.bicep with the known-good immutable digest from the SAME registry:

```bash
export PREVIOUS_IMAGE='<registry>.azurecr.io/orders@sha256:<previous-digest>'
az deployment group what-if -g "$RESOURCE_GROUP" --template-file infra/worker.bicep \
  --parameters prefix="$PREFIX" image="$PREVIOUS_IMAGE"
# After review:
az deployment group create -g "$RESOURCE_GROUP" -n orders-worker \
  --template-file infra/worker.bicep --parameters prefix="$PREFIX" image="$PREVIOUS_IMAGE"
```

Single revision mode retires the old revision after rollout. Rolling back code does not roll
back ledger data or idempotency bindings. Avoid schema changes unless both forward and backward
read compatibility are designed and tested.

## Data retention and acceptance checks

There is no automated Table purge, and DLQ retention is not the primary queue's one-day TTL.
Removing inbox/order records can allow a historical message to produce a new business effect.
Define retention based on maximum replay horizons and legal requirements before adapting this
to a real workload. Backup/restore and regional recovery are not implemented by this project.

After a real sandbox deployment, verify all of the following separately from local unit tests:

1. A producer can send but cannot write the ledger; the worker can receive and write but cannot send.
2. A valid order becomes one ledger record and a second delivery does not create another.
3. An invalid body and a conflicting order are visible in the DLQ with the expected reason.
4. Scale-from-zero and scale-out stay inside the configured replica cap.
5. A deliberately interrupted worker converges to one order after redelivery.
6. Preview changes nothing; bounded replay sends before settling and a corrected payload obeys fingerprint rules.
7. The metric alert fires, logs are queryable, and any configured notification path works.
8. Cleanup deletes only the dedicated sandbox after secure evidence export and inventory review.
