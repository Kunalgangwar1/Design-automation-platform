# Existing SDD: Pre-Fulfilment Order Cancellation

**Document ID:** SDD-OMS-014  
**Status:** Approved  
**Decision owner:** Commerce Architecture  
**Related capability:** Cancel an order before warehouse allocation

## Context

Customer-support agents need to cancel an order before fulfilment begins. Cancellation must stop further processing, release reserved inventory, void or refund the payment as appropriate, notify the customer, and provide an audit trail.

## Decision

Order Service remains the workflow owner because it owns order state. It validates that the order is `PLACED` or `CONFIRMED`, changes the state to `CANCELLATION_PENDING`, and calls Payment Service with a stable idempotency key. Payment Service voids an uncaptured authorization or requests a full refund for a captured payment through the Payment Gateway.

After financial success, Order Service records `CANCELLED` in the Order Database and publishes `OrderCancelled`. Inventory Service consumes the event to release reservations. Notification Service consumes it to send the approved cancellation template. Neither consumer calls the Payment Service.

## Failure handling

- Validation failures leave the order unchanged.
- A definitive gateway rejection changes the order from `CANCELLATION_PENDING` to `CANCELLATION_FAILED` and records a reason code.
- A gateway timeout leaves the operation pending. Payment Service looks up the operation with the original idempotency key and accepts a signed gateway webhook.
- Event publication uses an outbox record written with the order-state transaction so a committed cancellation cannot lose its event.
- Consumers deduplicate events by event ID.

## Security and audit

Support agents require the `order:cancel` permission. Order Service records the agent ID, reason code, timestamps, previous status, final status, and correlation ID. Payment tokens and card data are not logged or included in events.

## Consequences

This design keeps business eligibility in Order Service and financial behavior in Payment Service. Asynchronous inventory and notification processing avoids extending the customer-support request, but the portal must show that those downstream actions may complete shortly after the cancellation response.

## Relevance to future partial refunds

Partial refunds should reuse the ownership model, idempotency propagation, pending-payment reconciliation, transactional outbox, immutable audit history, and event-consumer deduplication. Unlike cancellation, the new capability must track refundable quantity and value per order line and must not change the completed order to a cancelled state.

