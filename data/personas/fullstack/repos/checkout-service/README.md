# checkout-service

Checkout and subscriptions rebuild for a media store.

## What it does
Checkout errors and a manual billing process were losing orders. This is the code from when I added subscription plans with proration.

## Stack
Stripe webhooks and checkout API; React, Node.js, Stripe, TypeScript.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: raised checkout conversion by 6%.
