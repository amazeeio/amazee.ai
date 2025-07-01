{{/*
Expand the name of the chart.
*/}}
{{- define "backend.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "backend.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "backend.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "backend.labels" -}}
helm.sh/chart: {{ include "backend.chart" . }}
{{ include "backend.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "backend.selectorLabels" -}}
app.kubernetes.io/name: {{ include "backend.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Backend-specific database URL generation.
This provides a backend-specific override if needed.
*/}}
{{- define "backend.databaseUrl" -}}
{{- include "amazee-ai.backendDatabaseUrl" . }}
{{- end }}

{{/*
Generate database host for backend service.
*/}}
{{- define "backend.databaseHost" -}}
{{- $postgresql := .Values.postgresql | default dict -}}
{{- $external := $postgresql.external | default dict -}}
{{- if $external.enabled }}
{{- /* Extract host from external URL - simplified approach */ -}}
{{- $url := $external.url }}
{{- $host := regexReplaceAll "postgresql://[^:]+:[^@]+@([^:]+):[^/]+/[^?]*" $url "${1}" }}
{{- $host }}
{{- else }}
{{- include "amazee-ai.postgresqlServiceName" . }}
{{- end }}
{{- end }}

{{/*
Generate database port for backend service.
*/}}
{{- define "backend.databasePort" -}}
{{- $postgresql := .Values.postgresql | default dict -}}
{{- $external := $postgresql.external | default dict -}}
{{- if $external.enabled }}
{{- /* Extract port from external URL - simplified approach */ -}}
{{- $url := $external.url }}
{{- $port := regexReplaceAll "postgresql://[^:]+:[^@]+@[^:]+:([^/]+)/[^?]*" $url "${1}" }}
{{- $port }}
{{- else }}
{{- include "amazee-ai.postgresqlPort" . }}
{{- end }}
{{- end }}

{{/*
Generate database name for backend service.
*/}}
{{- define "backend.databaseName" -}}
{{- $postgresql := .Values.postgresql | default dict -}}
{{- $external := $postgresql.external | default dict -}}
{{- if $external.enabled }}
{{- /* Extract database name from external URL - simplified approach */ -}}
{{- $url := $external.url }}
{{- $db := regexReplaceAll "postgresql://[^:]+:[^@]+@[^:]+:[^/]+/([^?]*)" $url "${1}" }}
{{- $db }}
{{- else }}
{{- include "amazee-ai.postgresqlDatabase" . }}
{{- end }}
{{- end }}

{{/*
Generate database username for backend service.
*/}}
{{- define "backend.databaseUsername" -}}
{{- $postgresql := .Values.postgresql | default dict -}}
{{- $external := $postgresql.external | default dict -}}
{{- if $external.enabled }}
{{- /* Extract username from external URL - simplified approach */ -}}
{{- $url := $external.url }}
{{- $user := regexReplaceAll "postgresql://([^:]+):[^@]+@[^:]+:[^/]+/[^?]*" $url "${1}" }}
{{- $user }}
{{- else }}
{{- include "amazee-ai.postgresqlUsername" . }}
{{- end }}
{{- end }}

{{/*
Generate database password for backend service.
*/}}
{{- define "backend.databasePassword" -}}
{{- $postgresql := .Values.postgresql | default dict -}}
{{- $external := $postgresql.external | default dict -}}
{{- if $external.enabled }}
{{- /* Extract password from external URL - simplified approach */ -}}
{{- $url := $external.url }}
{{- $pass := regexReplaceAll "postgresql://[^:]+:([^@]+)@[^:]+:[^/]+/[^?]*" $url "${1}" }}
{{- $pass }}
{{- else }}
{{- include "amazee-ai.postgresqlPassword" . }}
{{- end }}
{{- end }}