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

## Inbound routing (`ingress`)

Off by default. Turn it on only when something outside the cluster needs to
push alerts in — PagerDuty, Rootly, Alertmanager, Grafana. Slack, Telegram and
Discord need nothing here; the gateway dials out.

Pick one renderer with `ingress.kind`:

| `kind` | Renders | Needs |
| --- | --- | --- |
| `none` (default) | nothing | — |
| `ingress` | `networking.k8s.io/v1` Ingress | an ingress controller |
| `istio` | `networking.istio.io/v1` VirtualService, and a Gateway if you ask for one | Istio CRDs |
| `gateway-api` | `gateway.networking.k8s.io/v1` HTTPRoute | Gateway API CRDs |

On a shared cluster the platform team usually already runs a Gateway that
terminates TLS for a wildcard host. Attach to it rather than creating another:

```yaml
ingress:
  kind: istio
  host: opensre.example.com
  istio:
    gateways: [istio-ingress/default]   # namespace/name of the existing Gateway
```

Standalone, with its own Gateway and certificate:

```yaml
ingress:
  kind: istio
  host: opensre.example.com
  istio:
    gateway:
      create: true
      selector: { istio: ingressgateway }   # must match the gateway pods' labels
      tls: { credentialName: opensre-tls }  # Secret in the GATEWAY's namespace
```

Gateway API, for clusters that have the CRDs:

```yaml
ingress:
  kind: gateway-api
  host: opensre.example.com
  gatewayApi:
    parentRefs:
      - { name: default, namespace: istio-ingress, sectionName: https }
```

The referenced Gateway must allow routes from this release's namespace
(`listeners[].allowedRoutes`), which is a setting on the Gateway, not here.

### Attaching to a shared Gateway: check the certificate, not just the host

A Gateway that host-matches `*.example.com` does **not** necessarily hold a
wildcard certificate. Istio matches the hostname and the TLS credential is a
separate object, so the route can be perfectly configured and HTTPS still fails
because the certificate's SAN list has no entry for your host. The symptom is a
TLS error or a proxy `502` — not a `404` — and plain `:80` works fine, which
makes it look like a certificate problem somewhere else.

```bash
openssl s_client -connect <gateway-ip>:443 -servername <your-host> </dev/null 2>/dev/null \
  | openssl x509 -noout -text | grep -A1 'Subject Alternative Name'
```

Run it from outside any TLS-inspecting corporate proxy, or you will read the
proxy's re-signed certificate instead of the origin's. Adding your host means
editing the Gateway owner's certificate — often a cert-manager `Certificate`
under GitOps with `selfHeal`, where a live `kubectl edit` is silently reverted.

### Only `/alerts` is routed

`ingress.paths` defaults to `["/alerts"]` and is an allowlist. The web app also
serves `/health`, `/healthz`, `/readyz` and `/ok`, and **none of those are
token-gated** — routing `/` publishes cluster health to anyone who resolves the
hostname. Add `/investigate` only deliberately: it runs a full LLM
investigation synchronously, so every request costs money and occupies a
worker until it finishes.

### The token is not optional

`/alerts` and `/investigate` are gated by `OPENSRE_ALERT_LISTENER_TOKEN`
(a plain bearer token, compared with `hmac.compare_digest`). Unset, both routes
serve loopback callers only and answer everyone else `403` — so an ingress
without the token exposes a route that cannot work.

```yaml
secret:
  data:
    OPENSRE_ALERT_LISTENER_TOKEN: "<32+ random bytes>"
```

The chart **fails to render** if `ingress.kind` is set while a chart-rendered
Secret has no token. With `secret.existingSecret` it cannot look, so it warns
in `NOTES.txt` instead. Senders authenticate with:

```
Authorization: Bearer <token>
```

Setting the token also changes local behaviour: once it is set, loopback
callers must present it too.

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
| `ingress.kind` | `none` | `none` / `ingress` / `istio` / `gateway-api` |
| `ingress.host` | `""` | external hostname; required unless `kind: none` |
| `ingress.paths` | `["/alerts"]` | path prefixes routed — an allowlist |
| `secret.create` / `secret.existingSecret` | `true` / `""` | render Secret vs reference one |
| `postgresql.uri` / `postgresql.bundled` | `""` / `false` | external vs bundled Postgres |
| `redis.uri` / `redis.bundled` | `""` / `false` | external vs bundled Redis |
| `serviceAccount.annotations` | `{}` | Workload Identity / IRSA |
| `rbac.create` / `rbac.clusterRead` | `false` / `false` | optional read-only RBAC |
