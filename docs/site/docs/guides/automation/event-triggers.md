---
sidebar_position: 2
title: Event Triggers
---

# Event Triggers

Event triggers start agent runs when external events occur. You create a trigger, CubeBox gives you a webhook URL, and any service that can send HTTP webhooks (GitHub, Slack, Stripe, your own backend) can fire it.

## How it works

1. You create a trigger in CubeBox and define what should happen when an event arrives.
2. CubeBox generates a **webhook URL** and an **HMAC secret**.
3. You configure the external service to send webhooks to that URL, using the HMAC secret for signature verification.
4. When an event hits the webhook, CubeBox verifies the signature, applies your filter conditions, and starts an agent run with the event payload as context.

## Creating a trigger

Navigate to your workspace, open **Triggers** from the sidebar, and click **New Trigger**. Fill in:

| Field | Description |
|---|---|
| **Name** | A descriptive label (e.g., "GitHub issue triage"). |
| **Source type** | The kind of webhook. Currently supports **Generic webhook** (works with any service that sends JSON payloads). |
| **Filter conditions** | Optional rules to decide which events should fire the agent. See [filtering events](#filtering-events). |
| **Prompt** | The message sent to the agent. You can reference the event payload in the prompt. |
| **Target conversation** | Where the agent run happens. Same options as [scheduled tasks](./scheduled-tasks.md#conversation-options): fixed conversation or new conversation per event. |
| **Run identity** | Which user the agent run executes as. This controls the agent's permissions and tool access. |

After saving, CubeBox displays the **webhook URL** and **HMAC secret**. Copy both -- you will need them when configuring the external service.

## Webhook URL and HMAC verification

The webhook URL is a public endpoint (no authentication token in the URL), but every request must include an HMAC signature computed from the request body and the shared secret.

CubeBox verifies the signature on every inbound request. If the signature is missing or invalid, the request is rejected with a `401` response. This ensures that only the service holding your HMAC secret can trigger agent runs.

**How to configure your external service:**

Most webhook-capable services (GitHub, Stripe, etc.) have a "secret" field in their webhook settings. Paste the HMAC secret there. The service will include the signature in a header (e.g., `X-Hub-Signature-256` for GitHub), and CubeBox validates it automatically.

For custom integrations, compute the HMAC-SHA256 of the raw request body using the secret, and send it in the `X-Webhook-Signature` header.

## Filtering events

Not every webhook delivery should trigger an agent run. Filter conditions let you narrow down which events fire, using declarative field matchers on the incoming JSON payload.

**Example filters:**

| Condition | Effect |
|---|---|
| `event.action == "opened"` | Only fire when a new item is opened (e.g., GitHub issue opened). |
| `event.repository.full_name == "acme/api"` | Only fire for events from a specific repository. |
| `event.pull_request.draft == false` | Ignore draft pull requests. |

You can combine multiple conditions -- all conditions must match for the trigger to fire (AND logic).

If no filter conditions are set, every valid webhook delivery fires the trigger.

## Rate limiting and deduplication

CubeBox protects against accidental floods and duplicate deliveries:

- **Rate limiting** -- if a trigger receives events faster than the agent can process them, excess events are queued and processed in order. Sustained excessive volume is throttled.
- **Deduplication** -- if the same event is delivered multiple times (common with webhook retry mechanisms), CubeBox detects the duplicate and processes it only once.
- **Retry with backoff** -- if an agent run fails (e.g., transient model error), CubeBox retries the run with exponential backoff before marking it as failed.

## Event log

Each trigger has an event log that shows all received webhook deliveries and their processing status:

| Status | Meaning |
|---|---|
| **Processed** | Event matched filters, agent run completed successfully. |
| **Filtered** | Event was received but did not match filter conditions. No agent run started. |
| **Failed** | Agent run started but failed. Check the linked conversation for details. |
| **Rejected** | Signature verification failed. The request was not processed. |

Use the event log to verify that your webhook integration is working, debug filter conditions, and monitor trigger health.

## Example: GitHub issue triage

**Goal:** When a new issue is opened in your repository, the agent automatically triages it -- adds labels, assigns a priority, and posts a summary comment.

1. Go to **Triggers** and click **New Trigger**.
2. Name it "GitHub issue triage".
3. Source type: **Generic webhook**.
4. Add a filter condition: `event.action == "opened"`.
5. Set the prompt:
   > A new GitHub issue was just opened. Read the issue title and body from the event payload. Based on the content, assign appropriate labels (bug, feature, docs, etc.), estimate priority (P0-P3), and post a triage comment on the issue summarizing your assessment and suggested next steps.
6. Choose **new conversation per event** so each issue gets its own clean context.
7. Save and copy the webhook URL and HMAC secret.
8. In your GitHub repository, go to **Settings > Webhooks > Add webhook**:
   - Paste the webhook URL.
   - Set content type to `application/json`.
   - Paste the HMAC secret.
   - Select "Issues" under events.
9. Save the webhook. The next time someone opens an issue, CubeBox receives the event and the agent triages it.

## Example: Slack alert escalation

**Goal:** When your monitoring system sends a Slack-formatted webhook for a critical alert, the agent investigates and posts findings.

1. Create a trigger named "Critical alert investigation".
2. Add a filter condition: `event.severity == "critical"`.
3. Set the prompt:
   > A critical alert was triggered. Investigate the alert details from the event payload, check relevant logs and metrics if tools are available, and provide a preliminary root cause analysis with recommended next steps.
4. Choose a **fixed conversation** so the agent can correlate across multiple alerts.
5. Configure your monitoring system to send webhooks to the trigger URL when critical alerts fire.

## Tips

- **Keep the HMAC secret secure.** Treat it like a password. If compromised, delete the trigger and create a new one.
- **Use filters to reduce noise.** Broad triggers (no filters) on high-volume webhooks will generate many agent runs and consume model tokens quickly.
- **Test with a manual webhook.** Before connecting a live service, send a test payload with `curl` to verify your trigger and filters work:
  ```bash
  curl -X POST https://your-cubebox-instance/api/v1/webhooks/trg_abc123 \
    -H "Content-Type: application/json" \
    -H "X-Webhook-Signature: $(echo -n '{"test": true}' | openssl dgst -sha256 -hmac 'your-secret' | cut -d' ' -f2)" \
    -d '{"test": true}'
  ```
- **Monitor the event log.** Check the event log after connecting a new service to verify events are arriving and being processed as expected.
- **Choose the right conversation strategy.** Use new conversations for independent events (issue triage). Use a fixed conversation when correlation across events matters (alert investigation).
