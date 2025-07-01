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
{{- $backend := .Values.backend | default dict -}}
{{- $database := $backend.database | default dict -}}
{{- if $database.url }}
{{- $database.url | quote }}
{{- else }}
{{- /* Generate database URL using PostgreSQL configuration */ -}}
{{- $postgresql := .Values.postgresql | default dict -}}
{{- $external := $postgresql.external | default dict -}}
{{- if $external.enabled }}
{{- $external.url | quote }}
{{- else }}
{{- $host := printf "%s-postgresql" .Release.Name }}
{{- $user := "postgres" }}
{{- $auth := $postgresql.auth | default dict -}}
{{- $password := $auth.postgresPassword | default "postgres" }}
{{- $database_name := $auth.database | default "postgres_service" }}
{{- $port := "5432" }}
{{- printf "postgresql://%s:%s@%s:%s/%s" $user $password $host $port $database_name | quote }}
{{- end }}
{{- end }}
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
{{- printf "%s-postgresql" .Release.Name }}
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
{{- "5432" }}
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
{{- $auth := $postgresql.auth | default dict -}}
{{- $auth.database | default "postgres_service" }}
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
{{- "postgres" }}
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
{{- $auth := $postgresql.auth | default dict -}}
{{- $auth.postgresPassword | default "postgres" }}
{{- end }}
{{- end }}