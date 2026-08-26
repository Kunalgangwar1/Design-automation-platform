# Requirement: Item-Level Partial Refunds

**Requirement ID:** OMS-REF-001  
**Owner:** Customer Operations  
**Priority:** High  
**Status:** Proposed

## Business statement

Enable partial refunds for individual items of a completed order.

Customer-support agents need to select one or more order lines, specify a quantity and reason, and return the eligible amount to the original payment method. A customer may request more than one partial refund, but the cumulative refunded quantity and amount must never exceed what was purchased and captured.

## Business rules

1. Only orders in `COMPLETED` status are eligible.
2. Refunds are selected by order-line identifier and quantity; an entire order need not be refunded.
3. Refund amount equals the selected item subtotal plus allocated tax, less allocated discounts. Shipping is not refunded in the first release.
4. A line cannot be refunded beyond its purchased quantity.
5. The total refunded amount cannot exceed the payment's captured amount.
6. Each request must include a reason code and the support agent's identifier.
7. Duplicate submissions with the same idempotency key must return the original result without issuing another gateway refund.
8. Successful and failed attempts must be auditable.

## Acceptance criteria

- An agent can refund one unit from a multi-quantity order line on a completed order.
- The remaining refundable quantity and amount are visible after each successful refund.
- Invalid quantities, ineligible order states, and over-refunds are rejected before the payment gateway is called.
- A successful gateway refund is recorded against the relevant order lines.
- Inventory receives the refunded quantities and disposition so it can decide whether to restock them.
- The customer receives a notification containing the order number, refunded items, amount, and expected settlement time.
- A gateway timeout produces a retryable result and does not create an unverified successful refund.

## Non-functional expectations

- The operation must be idempotent.
- Refund history must be retained for seven years for audit purposes.
- Personally identifiable information must not appear in application logs or events.
- The synchronous agent response target is 3 seconds at the 95th percentile, excluding an explicitly reported pending gateway state.
- Existing full-order cancellation and pre-fulfilment void flows must remain unchanged.

## Open questions

- Which return reason codes allow inventory restocking?
- Is manager approval required above a monetary threshold?
- Should shipping refunds be supported in a later release?
- How should refunds be handled when the original card is expired?

