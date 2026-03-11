# Stripe local testing: webhooks & Checkout (local dev guide)

This document explains how to test Stripe Checkout and webhooks locally. It covers the recommended Stripe CLI workflow, an ngrok alternative, example commands (including a Checkout session with metadata), signature verification notes, Hono-specific raw-body handling, idempotency/retries, and unit-testing suggestions.

## Quick summary
- Recommended: use the Stripe CLI (`stripe listen --forward-to`) to forward real Stripe test events to your local server and get a temporary webhook signing secret to verify signatures.
- Alternative: expose your local server with ngrok and register the public URL in the Stripe Dashboard.
- For local Hono/webhook handlers: always verify the Stripe signature against the raw request body (bytes) using the webhook secret.
- For integration testing of checkout flows that include metadata, create a test Checkout Session via the Stripe API (test secret key) and complete it using Stripe test cards.

---

## Prerequisites
- Stripe account (test mode).
- Stripe CLI installed (recommended): https://stripe.com/docs/stripe-cli
  - macOS (Homebrew): brew install stripe/stripe-cli
- Local server running (e.g. Hono or FastAPI) and a webhook route (e.g. POST /api/stripe/webhook).
- Your app must verify webhook signatures using the Stripe signing secret (printed by the Stripe CLI or from the Dashboard if using a public endpoint).

---

## Recommended local flow (Stripe CLI)

1. Start your local server so the webhook endpoint is reachable at:
   - http://localhost:3000/api/stripe/webhook (example)

2. Start the Stripe CLI listener and forward events to your local endpoint:
   - stripe listen --forward-to http://localhost:3000/api/stripe/webhook
   - The CLI prints a webhook signing secret (whsec_...). Copy this and set it as STRIPE_WEBHOOK_SECRET in your environment so your local app can verify signatures.

3. Generate test events:
   - Quick trigger (no metadata):
     - stripe trigger checkout.session.completed
   - Real test Checkout (recommended for metadata testing):
     - Create a Checkout Session that includes metadata (teamId and regionId) using the Stripe API (test secret key). Example (curl):
       curl -X POST https://api.stripe.com/v1/checkout/sessions \
         -u sk_test_YOUR_TEST_KEY: \
         -d "success_url=http://localhost:3000/success" \
         -d "cancel_url=http://localhost:3000/cancel" \
         -d "payment_method_types[]"=card \
         -d "mode=payment" \
         -d "line_items[0][price]=price_YOUR_PRICE" \
         -d "line_items[0][quantity]=1" \
         -d "metadata[teamId]=123" \
         -d "metadata[regionId]=7"
     - The call returns a checkout session URL. Open it locally and finish checkout with Stripe test cards (e.g. 4242 4242 4242 4242).
     - Stripe will emit a `checkout.session.completed` event that the Stripe CLI forwards to your local webhook.

4. Verify forwarded events in the stripe CLI logs — the CLI shows forwarded events and any errors.

---

## Alternative: ngrok + Stripe Dashboard
1. Start your app locally (e.g. port 3000).
2. Run: ngrok http 3000
3. In the Stripe Dashboard → Developers → Webhooks, add the public URL given by ngrok (e.g. https://abc123.ngrok.io/api/stripe/webhook).
4. Use Stripe Dashboard test tools or create Checkout sessions as above; events are delivered to your public ngrok URL.
- Note: With ngrok, you must copy the webhook signing secret from the Dashboard (or the endpoint creation dialog) into your local env.

---

## Verifying webhook signatures
- Always verify the Stripe signature against the raw request body using the webhook signing secret.
- Do not parse JSON before verifying — you must use the raw bytes as received to compute the signature.
- Stripe libraries include helpers:
  - Node/TypeScript: stripe.webhooks.constructEvent(rawBody, sigHeader, webhookSecret)
  - Python: stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
- When using the Stripe CLI forwarder, the CLI re-signs forwarded events using the ephemeral signing secret it prints; use that secret locally while the CLI is running.

---

## Hono (or other JavaScript framework) notes — raw body access
- The webhook handler must access the raw request body. Example (Hono + TypeScript, conceptual):

  - Pseudocode:
    const payload = await request.arrayBuffer(); // raw bytes
    const sigHeader = request.headers.get('stripe-signature');
    try {
      const event = stripe.webhooks.constructEvent(Buffer.from(payload), sigHeader, STRIPE_WEBHOOK_SECRET);
      // handle event
    } catch (err) {
      // invalid signature -> return 400
    }

- Check your runtime (Node, Deno, Cloudflare Workers) for correct raw-body reading API. Hono docs have a stripe-webhook example.

---

## Idempotency & retries (recommended)
- Stripe retries failed webhooks; make your webhook processing idempotent.
- Strategies:
  - Persist processed Stripe event IDs or checkout session IDs (e.g. in a purchases table) and skip duplicates.
  - Use unique constraint on `stripe_session_id` to prevent double-processing.
  - Return 2xx only after you’ve persisted the processing outcome or queued background work; if you must do asynchronous work after acknowledging, record the intent so failures can be retried.
- Recommended design (lower cross-service fragility):
  - Preferred: Hono POSTs a single “purchase processed” payload to amazee.ai (single endpoint). amazee.ai performs the overwrite limit, sets last_budget_purchase_at, and triggers propagation atomically or via its own background job. This reduces brittle cross-service ordering issues.

---

## Testing retries & failure scenarios
- Simulate retries by:
  - Intentionally returning 500 from your webhook to ensure Stripe retries.
  - Using `stripe listen` + `stripe events resend` or replay from the Dashboard.
- Ensure your idempotency layer correctly ignores duplicates.

---

## Unit & CI testing recommendations
- Unit tests: mock Stripe event verification and LiteLLM/amazee.ai API calls; assert the webhook handler:
  - Verifies signature correctly (or calls stripe.webhooks helper),
  - Extracts metadata properly (teamId, regionId),
  - Calls downstream endpoints (or queues tasks) in the expected order.
- Integration/local-only tests: use stripe-cli triggers and live forwarding to your local dev server (not recommended in CI).
- If you want to run e2e in CI, consider mocking Stripe using test fixtures or a stub server that simulates Stripe events and signatures.

---

## Example workflow: full test (step-by-step)
1. Start your app:
   - npm run dev (server listening on :3000)
2. Start Stripe CLI:
   - stripe listen --forward-to http://localhost:3000/api/stripe/webhook
   - Copy the printed `Signing secret` (whsec_...) and export it:
     export STRIPE_WEBHOOK_SECRET="whsec_..."
3. Create a Checkout Session (curl example) — include metadata:
   - Replace `sk_test_YOUR_TEST_KEY` and `price_YOUR_PRICE`:
     curl -X POST https://api.stripe.com/v1/checkout/sessions \
       -u sk_test_YOUR_TEST_KEY: \
       -d "success_url=http://localhost:3000/success" \
       -d "cancel_url=http://localhost:3000/cancel" \
       -d "payment_method_types[]"=card \
       -d "mode=payment" \
       -d "line_items[0][price]=price_YOUR_PRICE" \
       -d "line_items[0][quantity]=1" \
       -d "metadata[teamId]=123" \
       -d "metadata[regionId]=7"
4. Open the returned URL, complete with test card.
5. Watch the Stripe CLI and local app logs for the forwarded webhook, signature verification, and subsequent handling.

---

## Useful links
- Stripe CLI: https://stripe.com/docs/stripe-cli
- Stripe webhooks signature docs: https://stripe.com/docs/webhooks/signatures
- Hono docs / stripe example: https://hono.dev/examples/ (search for stripe-webhook)
- ngrok: https://ngrok.com/
