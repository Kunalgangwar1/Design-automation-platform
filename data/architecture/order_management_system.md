# Existing Order Management System Architecture

**Document owner:** Commerce Architecture  
**Last reviewed:** 2026-07-15  
**Environment:** Production reference architecture

## Components

### Order Service

The Order Service owns order state and is the authoritative business orchestrator. It exposes operations used by customer-support tooling, validates allowed state transitions, calculates order totals, and writes orders and line items to the Order Database. It currently supports pre-fulfilment cancellation but has no item-level post-completion refund workflow.

### Payment Service

The Payment Service owns payment transactions. It stores internal payment and refund references, translates internal requests into the external gateway contract, and prevents duplicate financial operations using an idempotency key. The service supports capture, void, and full refund; partial-refund support requires an item-independent amount-based refund command.

### Inventory Service

The Inventory Service owns stock availability and adjustments. It consumes order lifecycle events. For returns, it needs SKU, quantity, location, and disposition (`RESTOCK`, `DAMAGED`, or `DISCARD`) before changing available stock. A refund alone does not always imply restocking.

### Notification Service

The Notification Service owns customer email and SMS delivery. It consumes business events, resolves approved templates, and records delivery outcomes. It must not receive card details or internal support notes.

### Order Database

The Order Database is a relational data store owned exclusively by the Order Service. Existing logical tables are `orders`, `order_lines`, and `order_status_history`. The proposed refund capability will eventually require refund headers, refund lines, status history, idempotency keys, and gateway references, but no schema is implemented in this repository.

### External Payment Gateway

The Payment Gateway is a third-party HTTPS API. It accepts a payment reference, refund amount, currency, merchant reference, and idempotency key. It may return `SUCCEEDED`, `PENDING`, or `REJECTED`. Webhooks can later confirm pending transactions. Requests can time out even when the gateway has accepted them, so callers must reconcile rather than blindly retry with a new key.

## Current connections

| Source | Target | Interaction | Style |
| --- | --- | --- | --- |
| Support Portal | Order Service | Read and update orders | Synchronous HTTPS |
| Order Service | Order Database | Store order state and audit history | SQL transaction |
| Order Service | Payment Service | Capture, void, and refund commands | Synchronous HTTPS |
| Payment Service | Payment Gateway | Execute payment operations | Synchronous HTTPS |
| Order Service | Event Bus | Publish order lifecycle events | Asynchronous event |
| Event Bus | Inventory Service | Deliver inventory-relevant events | Asynchronous event |
| Event Bus | Notification Service | Deliver customer-notification events | Asynchronous event |
| Payment Gateway | Payment Service | Report pending-operation outcomes | Signed webhook |

## Ownership and constraints

- Services may not read another service's database directly.
- The Order Service owns refund business eligibility; the Payment Service owns financial execution.
- Events are at-least-once, so consumers must deduplicate by event ID.
- Correlation IDs travel through synchronous calls and events.
- Payment card data is tokenized by the gateway and is never stored in the Order Database.
- Audit data is immutable to application users and retained for seven years.

## Expected partial-refund interaction

1. The Support Portal sends selected order lines, quantities, reason, disposition, agent ID, and an idempotency key to the Order Service.
2. The Order Service loads the completed order, locks the refundable balances, and validates the request.
3. The Order Service calculates the refundable amount and calls the Payment Service.
4. The Payment Service calls the Payment Gateway using the same stable idempotency key.
5. The Order Service records the result in the Order Database.
6. After confirmed success, the Order Service publishes `OrderItemsRefunded`.
7. Inventory Service applies eligible stock adjustments; Notification Service informs the customer.

