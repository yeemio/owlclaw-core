---
name: create-order
description: Create order in upstream order service
metadata:
  tools_schema:
    create-order:
      description: Create order in upstream order service
      parameters:
        type: object
        properties:
          order_id:
            type: string
          amount:
            type: number
        required:
          - order_id
          - amount
      binding:
        type: http
        method: POST
        url: https://api.example.com/orders
        headers:
          X-API-Key: ${XAPI_API_KEY}
        response_mapping:
          path: $.data
          status_codes:
            "201": success
owlclaw:
  prerequisites:
    env:
      - XAPI_API_KEY
---

# Instructions

## Business Rules

1. Only create an order after the user confirms total amount and currency.
2. Reject requests with non-positive `amount`.
3. If upstream returns non-2xx, explain retry policy to user before reattempt.

## Execution Notes

- Tool `create-order` is the only write operation in this skill.
- Always include `order_id` and `amount`.
