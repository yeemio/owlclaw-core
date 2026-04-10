---
name: order-http-shadow
description: Create order in shadow mode for zero-code comparison.
tools:
  create-order:
    description: Create order request in shadow mode
    order_id: string
    amount: number
    binding:
      type: http
      method: POST
      mode: shadow
      url: http://127.0.0.1:8008/orders
      body_template:
        order_id: "{order_id}"
        amount: "{amount}"
---

# Instructions

`create-order` is configured as `mode: shadow`.

- You may simulate create-order calls safely without side effects.
- Explain to user that shadow mode records intent but does not send write request.
- For production cutover, switch mode to `active` after shadow validation passes.
