{{/*
Expand the name of the chart.
*/}}
{{- define "frontend.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "frontend.fullname" -}}
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
{{- define "frontend.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "frontend.labels" -}}
helm.sh/chart: {{ include "frontend.chart" . }}
{{ include "frontend.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "frontend.selectorLabels" -}}
app.kubernetes.io/name: {{ include "frontend.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Frontend-specific API URL generation.
This provides a frontend-specific override if needed.
*/}}
{{- define "frontend.apiUrl" -}}
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
Generate backend service name for frontend.
*/}}
{{- define "frontend.backendServiceName" -}}
{{- printf "%s-backend" .Release.Name }}
{{- end }}

{{/*
Generate backend service port for frontend.
*/}}
{{- define "frontend.backendServicePort" -}}
{{- "8800" }}
{{- end }}

{{/*
Generate backend service URL for frontend.
*/}}
{{- define "frontend.backendServiceUrl" -}}
{{- $service := include "frontend.backendServiceName" . }}
{{- $port := include "frontend.backendServicePort" . }}
{{- printf "http://%s:%s" $service $port | quote }}
{{- end }}