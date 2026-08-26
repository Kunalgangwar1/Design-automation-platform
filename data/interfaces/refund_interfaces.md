# Existing and Proposed Refund Interfaces

This document describes contracts for design discussion only. It does not represent implemented endpoints or events.

## Support Portal to Order Service

Proposed command: `POST /orders/{order_id}/refunds`

```json
{
  "idempotency_key": "refund-ORD-10452-attempt-1",
  "agent_id": "agent-287",
  "reason_code": "ITEM_DAMAGED",
  "items": [
    {
      "order_line_id": "line-2",
      "quantity": 1,
      "disposition": "DAMAGED"
    }
  ]
}
```

The Order Service should return a refund identifier, calculated amount, currency, status, and remaining refundable balances. Expected domain errors include `ORDER_NOT_COMPLETED`, `QUANTITY_EXCEEDS_REFUNDABLE`, `AMOUNT_EXCEEDS_CAPTURED`, and `IDEMPOTENCY_CONFLICT`.

## Order Service to Payment Service

Proposed internal command: `POST /payments/{payment_id}/refunds`

Required fields are internal refund ID, amount in minor units, ISO currency, reason code, correlation ID, and idempotency key. The Payment Service does not receive product or customer details because it executes a financial amount, not order policy.

Example response:

```json
{
  "payment_refund_id": "payref-7831",
  "gateway_reference": "gw-ref-991823",
  "status": "SUCCEEDED",
  "amount_minor": 2499,
  "currency": "USD"
}
```

## OrderItemsRefunded event

The Order Service publishes this versioned event only after payment success is confirmed.

```json
{
  "event_id": "evt-55110",
  "event_type": "OrderItemsRefunded",
  "event_version": 1,
  "occurred_at": "2026-08-20T10:15:30Z",
  "correlation_id": "corr-44019",
  "order_id": "ORD-10452",
  "refund_id": "ref-1207",
  "amount_minor": 2499,
  "currency": "USD",
  "items": [
    {
      "order_line_id": "line-2",
      "sku": "HEADPHONES-BLK",
      "quantity": 1,
      "disposition": "DAMAGED"
    }
  ]
}
```

Inventory Service uses SKU, quantity, and disposition. Notification Service uses order ID, refund ID, item summary, amount, and currency. Consumers deduplicate deliveries by `event_id`. Agent IDs, support notes, and payment credentials are excluded from the event.

## Payment Gateway behavior

The gateway accepts refund amounts in minor currency units. A repeated request with the same merchant idempotency key returns the original operation. HTTP `202` represents `PENDING`, while an HTTP timeout is ambiguous and requires lookup by idempotency key. Signed webhook messages contain the gateway reference, merchant reference, final status, amount, currency, and event timestamp.

