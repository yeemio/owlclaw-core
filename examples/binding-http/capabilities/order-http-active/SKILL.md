---
name: order-http-active
description: Read order detail from HTTP API via declarative binding.
tools:
  fetch-order:
    description: Fetch one order by id
    order_id: string
    binding:
      type: http
      method: GET
      url: http://127.0.0.1:8008/orders/{order_id}
      response_mapping:
        path: $.data
---

# Instructions

Use `fetch-order` to query the latest order state.

- Always ask for `order_id` when it is missing.
- Return a short summary: order id, status, and amount.
- If API returns not found, ask user to re-check the order id.
