Useful commands for testing the Stripe integration from the CLI

## Setup:
Follow the [installation instructions](https://docs.stripe.com/stripe-cli#install), and then log in. If you have your test key, you can use that when you log in to ensure you're using it for everything.

## Testing:
You will need to have at least four terminal panes open. A multiplexer like tmux will make this super easy, but you can do new tabs or windows if you need to.
### Pane 1 - Logs:
Watch the logs for what you're sending to Stripe by running
```sh
stripe logs tail
```
### Pane 2 - Webhook:
Listen for stripe events, and forward them to your local testing environment:
```sh
stripe listen --forward-to localhost:8800/billing/events
```
You can optionally limit which events are forwarded, but the handler defaults to accepting anything and then choosing what to do with it in the background.
Running this command will give you the webhook secret you need to decode all events. make sure to copy it into the appropriate environment variable.
### Pane 3 - Backend service:
Stand up the service using docker compose, and watch the logs to see events being processed as they com in:
```sh
docker compose up --build -d
docker compose logs -f backend
```
### Pane 4 - Triggers:
Each of these triggers will initiate a different flow in the backend. USe them to ensure everything is still working as expected:

Add a product
```sh
stripe trigger checkout.session.completed --override checkout_session:customer=ccus_SQN5TNT4NxFgWW # forces a customer ID to be set
stripe trigger checkout.session.async_payment_succeeded --override checkout_session:customer=cus_SQN5TNT4NxFgWW
stripe trigger subscription.payment_succeeded --override subscription:customer=cus_SQN5TNT4NxFgWW
```

Remove a product
```sh
stripe trigger checkout.session.async_payment_failed --override checkout_session:customer=cus_SLpVFWQFHmls9T # Should add then remove
stripe trigger checkout.session.expired --override checkout_session:customer=cus_SLpVFWQFHmls9T # Will remove the product without first adding it.
stripe trigger subscription.payment_failed # Add then remove
stripe trigger customer.subscription.paused # Add then remove
stripe trigger customer.subscription.deleted # Add twice, then remove
```