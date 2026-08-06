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

### Slack requires an organization

Beyond the Slack tokens in the Secret, the gateway refuses every Slack turn
until it knows who owns the data that turn produces:

```yaml
config:
  extraEnv:
    ORGANIZATION_ID: my-org           # required; fails closed when missing
    OPENSRE_SILO_TEAM_IDS: T0123456   # recommended; allowlist of Slack teams
```

Without `ORGANIZATION_ID` the pod starts, connects to Socket Mode, and then
fails each message with `no organization is configured for this deployment` —
so the symptom appears only once someone types. Without
`OPENSRE_SILO_TEAM_IDS` any workspace that installs the app is served from this
organization's identity and inherits its credentials; set it to fail closed.
Details in [docs/principal-scoped-storage.mdx](../../../docs/principal-scoped-storage.mdx).

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

### Keyless LLM auth (Vertex AI / Bedrock)

`vertex-ai` and `bedrock` use ambient cloud credentials, so **no LLM key goes in
the Secret at all** — annotate the ServiceAccount instead:

```yaml
config:
  llmProvider: vertex-ai
  extraEnv:
    VERTEX_AI_PROJECT: my-gcp-project
    VERTEX_AI_LOCATION: global
    VERTEX_AI_REASONING_MODEL: claude-opus-5   # Claude served through Vertex
serviceAccount:
  create: true
  annotations:
    iam.gke.io/gcp-service-account: opensre@my-gcp-project.iam.gserviceaccount.com
```

The Google service account needs `roles/aiplatform.user` in the Vertex project,
and a `roles/iam.workloadIdentityUser` binding for
`<cluster-project>.svc.id.goog[<namespace>/<serviceaccount>]`. Those two project
names differ when the cluster and the Vertex entitlement live in separate
projects; cross-project Workload Identity works, but the binding is easy to miss
and the pod fails only on the first LLM call.
See [docs/llm-providers.mdx](../../../docs/llm-providers.mdx) for model IDs.

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

## GitOps (Argo CD)

Two ready-to-edit files ship with the chart:

- [`values-prod.yaml`](values-prod.yaml) — a secret-free production overrides
  file (existing Secret, external managed Postgres/Redis via the Secret, HPA,
  resource requests/limits). Safe to commit.
- [`../../argocd/opensre-application.yaml`](../../argocd/opensre-application.yaml)
  — an Argo CD `Application` that points at `deploy/helm/opensre` with
  `values-prod.yaml`, auto-sync + prune + self-heal.

```bash
# Edit repoURL (<you>) and image.repository, then:
kubectl apply -f deploy/argocd/opensre-application.yaml
```

Keep credentials out of `values-prod.yaml`; supply them through the
`existingSecret` (external-secrets / sealed-secrets).

## Common values

| Key | Default | Description |
| --- | --- | --- |
| `image.repository` / `image.tag` | `opensre` / appVersion | image |
| `config.llmProvider` | `anthropic` | LLM provider (key goes in the Secret; `vertex-ai`/`bedrock` need none) |
| `web.enabled` / `gateway.enabled` | `true` / `true` | which workloads to run |
| `web.autoscaling.enabled` | `false` | HPA for the web Deployment |
| `secret.create` / `secret.existingSecret` | `true` / `""` | render Secret vs reference one |
| `postgresql.uri` / `postgresql.bundled` | `""` / `false` | external vs bundled Postgres |
| `redis.uri` / `redis.bundled` | `""` / `false` | external vs bundled Redis |
| `serviceAccount.annotations` | `{}` | Workload Identity / IRSA |
| `rbac.create` / `rbac.clusterRead` | `false` / `false` | optional read-only RBAC |
