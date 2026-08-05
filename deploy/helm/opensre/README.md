# OpenSRE Helm chart

Deploy OpenSRE to Kubernetes as an HTTP investigator (`web`) and/or a
Slack/Telegram + scheduler daemon (`gateway`), from the repo's container image.

Built for GitOps: render/commit the chart to your manifests repo and let
Argo CD / Flux reconcile it.

## Prerequisites

- The OpenSRE image built and pushed somewhere your cluster can pull
  (`docker build -t <registry>/opensre:<tag> .` from the repo root).
- An LLM provider API key, plus any integration credentials (kubeconfig,
  Datadog, etc.).

## Quick start

```bash
helm install opensre deploy/helm/opensre \
  --namespace opensre --create-namespace \
  --set image.repository=<registry>/opensre \
  --set image.tag=<tag> \
  --set config.llmProvider=anthropic \
  --set secret.data.ANTHROPIC_API_KEY=sk-ant-...
```

## Modes

| Value | Workload | Networking | Use |
| --- | --- | --- | --- |
| `web.enabled` | Deployment + Service (+ optional HPA) | inbound HTTP `:8000` | health, `POST /alerts`, `POST /investigate` |
| `gateway.enabled` | Deployment (**1 replica**) | outbound long-poll, no Service | Slack/Telegram + scheduler |

Both can run together (default). The gateway is pinned to one replica because
Slack Socket Mode is single-consumer.

## Secrets

`secret.create=true` (default) renders a Secret from `secret.data`. **Do not
commit real values in a git-tracked values file.** For production, manage the
Secret out-of-band (external-secrets / sealed-secrets) and set:

```yaml
secret:
  create: false
  existingSecret: opensre-credentials
```

The pod `ServiceAccount` supports cloud IAM annotations (GKE Workload Identity,
AWS IRSA) for cloud integrations. OpenSRE's Kubernetes tools authenticate with a
kubeconfig supplied via the Secret (`KUBECONFIG_CONTENT`), not the in-cluster
token — register additional clusters with `opensre integrations add-cluster`.

## Persistence

Pick per backend — external managed URI (recommended) or a bundled single-replica pod:

```yaml
# External (recommended for production)
postgresql: { uri: "postgresql://user:pass@host:5432/opensre" }
redis:      { uri: "redis://host:6379/0" }

# Bundled (convenience; not HA, no backups)
postgresql: { bundled: true }
redis:      { bundled: true }
```

Precedence per backend: explicit `uri` > `bundled` > whatever is in the Secret.

## GitOps (Argo CD example)

Point an `Application` at this chart path in your fork and let Argo reconcile:

```yaml
source:
  repoURL: https://github.com/<you>/opensre
  path: deploy/helm/opensre
  targetRevision: main
  helm:
    valueFiles: [values-prod.yaml]   # your non-secret overrides
```

Keep credentials out of `values-prod.yaml`; use `existingSecret`.

## Common values

| Key | Default | Description |
| --- | --- | --- |
| `image.repository` / `image.tag` | `opensre` / appVersion | image |
| `config.llmProvider` | `anthropic` | LLM provider (key goes in the Secret) |
| `web.enabled` / `gateway.enabled` | `true` / `true` | which workloads to run |
| `web.autoscaling.enabled` | `false` | HPA for the web Deployment |
| `secret.create` / `secret.existingSecret` | `true` / `""` | render Secret vs reference one |
| `postgresql.uri` / `postgresql.bundled` | `""` / `false` | external vs bundled Postgres |
| `redis.uri` / `redis.bundled` | `""` / `false` | external vs bundled Redis |
| `serviceAccount.annotations` | `{}` | Workload Identity / IRSA |
| `rbac.create` / `rbac.clusterRead` | `false` / `false` | optional read-only RBAC |
