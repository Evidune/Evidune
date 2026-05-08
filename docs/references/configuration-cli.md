# Configuration CLI

Evidune supports first-run configuration without hand-editing `evidune.yaml`.

## Model Setup

```bash
evidune configure --section model \
  --provider openai \
  --model gpt-4o \
  --api-key-env OPENAI_API_KEY \
  --config evidune.yaml
```

The CLI stores the API key environment variable name in `agent.api_key_env`.
It does not write raw API keys.

## Onboarding

```bash
evidune onboard \
  --provider openai \
  --model gpt-4o \
  --api-key-env OPENAI_API_KEY \
  --channel web \
  --host 127.0.0.1 \
  --port 8081 \
  --config evidune.yaml
```

`onboard` creates a minimal config when the file does not exist, then configures
the model and an optional message gateway.

## Message Gateways

The user-facing `channels` CLI manages bidirectional message gateways under the
existing `gateways` key. It does not modify the outbound iteration-report
`channels` key.

```bash
evidune channels add cli --config evidune.yaml
evidune channels add web --host 127.0.0.1 --port 8081 --config evidune.yaml
evidune channels add feishu \
  --app-id-env FEISHU_APP_ID \
  --app-secret-env FEISHU_APP_SECRET \
  --config evidune.yaml
evidune channels list --config evidune.yaml
evidune channels remove web --config evidune.yaml
evidune channels test feishu --config evidune.yaml
```

Feishu secrets are written as `${ENV_VAR}` references. `channels test` and
`gateway status` report missing environment variables clearly and do not perform
live Feishu credential checks by default.

## Gateway Readiness

```bash
evidune gateway status --config evidune.yaml
```

`gateway status` validates configured gateways. The web gateway check calls
`/api/skills`; CLI is local-only; Feishu checks required fields and environment
references.
