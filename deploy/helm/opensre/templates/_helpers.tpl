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
