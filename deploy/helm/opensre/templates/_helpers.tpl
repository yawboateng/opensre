{{/* Chart name (respecting nameOverride). */}}
{{- define "opensre.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Fully qualified app name. */}}
{{- define "opensre.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "opensre.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Common labels. */}}
{{- define "opensre.labels" -}}
helm.sh/chart: {{ include "opensre.chart" . }}
{{ include "opensre.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "opensre.selectorLabels" -}}
app.kubernetes.io/name: {{ include "opensre.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "opensre.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "opensre.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* Name of the Secret both workloads read env from. */}}
{{- define "opensre.secretName" -}}
{{- if .Values.secret.existingSecret -}}
{{- .Values.secret.existingSecret -}}
{{- else -}}
{{- include "opensre.fullname" . -}}
{{- end -}}
{{- end -}}

{{- define "opensre.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) -}}
{{- end -}}

{{/* Resolved DATABASE_URI: explicit uri > bundled service > empty. */}}
{{- define "opensre.databaseUri" -}}
{{- if .Values.postgresql.uri -}}
{{- .Values.postgresql.uri -}}
{{- else if .Values.postgresql.bundled -}}
{{- printf "postgresql://%s:%s@%s-postgresql:5432/%s" .Values.postgresql.auth.username .Values.postgresql.auth.password (include "opensre.fullname" .) .Values.postgresql.auth.database -}}
{{- end -}}
{{- end -}}

{{/* Resolved REDIS_URI: explicit uri > bundled service > empty. */}}
{{- define "opensre.redisUri" -}}
{{- if .Values.redis.uri -}}
{{- .Values.redis.uri -}}
{{- else if .Values.redis.bundled -}}
{{- printf "redis://%s-redis:6379/0" (include "opensre.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/* Name of the web Service the ingress objects route to. */}}
{{- define "opensre.webServiceName" -}}
{{- printf "%s-web" (include "opensre.fullname" .) -}}
{{- end -}}

{{/* Validated ingress.kind, lowercased. Empty values mean `none`. */}}
{{- define "opensre.ingressKind" -}}
{{- $kind := default "none" .Values.ingress.kind | lower -}}
{{- $known := list "none" "ingress" "istio" "gateway-api" -}}
{{- if not (has $kind $known) -}}
{{- fail (printf "ingress.kind must be one of %s, got %q" (join "|" $known) $kind) -}}
{{- end -}}
{{- $kind -}}
{{- end -}}

{{/*
Preconditions shared by all three ingress renderers. Renders nothing; called
for its `fail`s. Each check guards a configuration that installs cleanly and
is then broken (or unsafe) at runtime, which is the expensive way to find out.
*/}}
{{- define "opensre.validateIngress" -}}
{{- if not .Values.web.enabled -}}
{{- fail "ingress.kind is set but web.enabled is false — there is no Service to route to." -}}
{{- end -}}
{{- if not .Values.ingress.host -}}
{{- fail "ingress.host is required when ingress.kind is not `none`." -}}
{{- end -}}
{{- if not .Values.ingress.paths -}}
{{- fail "ingress.paths must list at least one path prefix (e.g. /alerts)." -}}
{{- end -}}
{{/*
Without OPENSRE_ALERT_LISTENER_TOKEN the alert routes answer every
non-loopback caller with 403, so an ingress would publish an endpoint that
cannot work. Only checkable when this chart renders the Secret — with
`existingSecret` the contents are out-of-band, so NOTES.txt warns instead.
*/}}
{{- if and .Values.secret.create (not .Values.secret.existingSecret) -}}
{{- if not (get .Values.secret.data "OPENSRE_ALERT_LISTENER_TOKEN") -}}
{{- fail "ingress.kind is set but secret.data.OPENSRE_ALERT_LISTENER_TOKEN is empty — /alerts and /investigate would reject every caller through the ingress with 403. Set the token, or use secret.existingSecret to supply it out-of-band." -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Shared env for both workloads. `mode` is passed by the caller via a dict:
{{ include "opensre.env" (dict "root" . "mode" "web") }}
*/}}
{{- define "opensre.env" -}}
{{- $root := .root -}}
- name: MODE
  value: {{ .mode | quote }}
- name: LLM_PROVIDER
  value: {{ $root.Values.config.llmProvider | quote }}
{{- if eq .mode "web" }}
- name: PORT
  value: {{ $root.Values.web.containerPort | quote }}
{{- end }}
{{- with (include "opensre.databaseUri" $root) }}
- name: DATABASE_URI
  value: {{ . | quote }}
{{- end }}
{{- with (include "opensre.redisUri" $root) }}
- name: REDIS_URI
  value: {{ . | quote }}
{{- end }}
{{- range $key, $value := $root.Values.config.extraEnv }}
- name: {{ $key }}
  value: {{ $value | quote }}
{{- end }}
{{- end -}}
