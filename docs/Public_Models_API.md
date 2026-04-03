# Public Models API

## Overview

The `/public/models` endpoint returns a list of AI models available across all active, non-dedicated regions. It aggregates model information from each region's LiteLLM instance and presents it in a unified response.

## Endpoint

```
GET /public/models
```

## Authentication

**No authentication required** - This is a public endpoint listed in the public paths configuration.

## Request

No request body, query parameters, or headers are required.

### Example Request

```bash
curl -X 'GET' \
  'https://api.amazee.io/public/models' \
  -H 'accept: application/json' | jq
```

## Response

### Success Response (200 OK)

Returns a JSON array of region objects, each containing its model catalog.

```json
[
  {
    "region": "amazeeai-us1",
    "status": "ga",
    "models": [
      {
        "model_id": "claude-4-5-sonnet",
        "display_name": "Claude 4 5 Sonnet",
        "provider": "aws",
        "type": "chat",
        "context_length": 1000000,
        "max_output_tokens": 64000,
        "capabilities": {
          "supports_vision": true,
          "supports_function_calling": true,
          "supports_reasoning": true,
          "supports_prompt_caching": true
        },
        "pricing": {
          "input_cost_per_token": 'n/a',
          "output_cost_per_token": 'n/a'
        }
      }
    ]
  },
  {
    "region": "amazeeai-de1",
    "status": "unavailable",
    "models": []
  }
]
```

### Response Schema

| Field | Type | Description |
|-------|------|-------------|
| `region` | `string` | Region identifier (e.g. `amazeeai-us1`, `amazeeai-uk1`) |
| `status` | `string` | Region status: `ga` (available) or `unavailable` |
| `models` | `array` | List of model summaries for the region |

#### Model Summary

| Field | Type | Description |
|-------|------|-------------|
| `model_id` | `string` | Unique model identifier |
| `display_name` | `string` | Human-readable model name |
| `provider` | `string` | Infrastructure provider: `aws`, `gcp`, `azure`, or `other` |
| `type` | `string` | Model mode forwarded from LiteLLM's `model_info.mode`; commonly `chat`, `embedding`, or `image_generation`, with `other` used as a fallback when no mode is available |
| `context_length` | `integer` | Maximum input context length in tokens |
| `max_output_tokens` | `integer` | Maximum output tokens (null for embeddings) |
| `capabilities` | `object` | Capability flags (see below) |
| `pricing` | `object` | Per-token pricing (see below) NOTE: using `n/a` for now |

#### Capabilities

| Field | Type | Description |
|-------|------|-------------|
| `supports_vision` | `boolean` | Accepts image inputs |
| `supports_function_calling` | `boolean` | Supports tool/function calling |
| `supports_reasoning` | `boolean` | Supports extended reasoning |
| `supports_prompt_caching` | `boolean` | Supports prompt caching |

#### Pricing

| Field | Type | Description |
|-------|------|-------------|
| `input_cost_per_token` | `float` | Cost per input token in USD (NOTE: using `n/a` for now) |
| `output_cost_per_token` | `float` | Cost per output token in USD (NOTE: using `n/a` for now) |

## Caching

The endpoint implements caching at two layers:

### Server-side (application cache)

- An in-memory cache stores the aggregated region data with a **1-hour TTL** (`_CACHE_TTL`).
- A double-checked locking pattern using `asyncio.Lock` prevents cache stampedes - only one request rebuilds the cache when it expires.
- If an individual region's LiteLLM instance is unreachable (timeout: 10s per region), that region is returned with `status: "unavailable"` and an empty `models` array. Other regions are unaffected.

### Client-side (HTTP cache headers)

- On success (status < 400): `Cache-Control: public, max-age=3600`
- On error: `Cache-Control: no-store`

This means downstream clients and CDNs may cache the response for up to 1 hour.
