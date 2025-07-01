{{/*
Expand the name of the chart.
*/}}
{{- define "amazee-ai.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "amazee-ai.fullname" -}}
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
{{- define "amazee-ai.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "amazee-ai.labels" -}}
helm.sh/chart: {{ include "amazee-ai.chart" . }}
{{ include "amazee-ai.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "amazee-ai.selectorLabels" -}}
app.kubernetes.io/name: {{ include "amazee-ai.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Generate database connection string for PostgreSQL.
If external database is enabled, use the provided URL.
Otherwise, generate a connection string using the internal PostgreSQL service.
*/}}
{{- define "amazee-ai.databaseUrl" -}}
{{- $postgresql := .Values.postgresql | default dict -}}
{{- $external := $postgresql.external | default dict -}}
{{- if $external.enabled }}
{{- $external.url | quote }}
{{- else }}
{{- $host := printf "%s-postgresql" .Release.Name }}
{{- $user := "postgres" }}
{{- $auth := $postgresql.auth | default dict -}}
{{- $password := $auth.postgresPassword | default "postgres" }}
{{- $database := $auth.database | default "postgres_service" }}
{{- $port := "5432" }}
{{- printf "postgresql://%s:%s@%s:%s/%s" $user $password $host $port $database | quote }}
{{- end }}
{{- end }}

{{/*
Generate database connection string for backend service.
This function checks if a specific database URL is provided in backend.database.url,
and if not, falls back to the main database URL generation.
*/}}
{{- define "amazee-ai.backendDatabaseUrl" -}}
{{- $backend := .Values.backend | default dict -}}
{{- $database := $backend.database | default dict -}}
{{- if $database.url }}
{{- $database.url | quote }}
{{- else }}
{{- include "amazee-ai.databaseUrl" . }}
{{- end }}
{{- end }}

{{/*
Generate API URL for frontend service.
If a specific API URL is provided, use it.
Otherwise, generate a URL using the backend service name.
*/}}
{{- define "amazee-ai.apiUrl" -}}
{{- $frontend := .Values.frontend | default dict -}}
{{- if $frontend.apiUrl }}
{{- $frontend.apiUrl | quote }}
{{- else }}
{{- $backendService := printf "%s-backend" .Release.Name }}
{{- $port := "8800" }}
{{- printf "http://%s:%s" $backendService $port | quote }}
{{- end }}
{{- end }}

{{/*
Generate PostgreSQL service name for internal database.
*/}}
{{- define "amazee-ai.postgresqlServiceName" -}}
{{- printf "%s-postgresql" .Release.Name }}
{{- end }}

{{/*
Generate PostgreSQL service port.
*/}}
{{- define "amazee-ai.postgresqlPort" -}}
{{- "5432" }}
{{- end }}

{{/*
Generate PostgreSQL database name.
*/}}
{{- define "amazee-ai.postgresqlDatabase" -}}
{{- $postgresql := .Values.postgresql | default dict -}}
{{- $auth := $postgresql.auth | default dict -}}
{{- $auth.database | default "postgres_service" }}
{{- end }}

{{/*
Generate PostgreSQL username.
*/}}
{{- define "amazee-ai.postgresqlUsername" -}}
{{- "postgres" }}
{{- end }}

{{/*
Generate PostgreSQL password.
*/}}
{{- define "amazee-ai.postgresqlPassword" -}}
{{- $postgresql := .Values.postgresql | default dict -}}
{{- $auth := $postgresql.auth | default dict -}}
{{- $auth.postgresPassword | default "postgres" }}
{{- end }}