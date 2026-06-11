{{/* vim: set filetype=mustache: */}}
{{- define "cubebox.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cubebox.fullname" -}}
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

{{- define "cubebox.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "cubebox.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "cubebox.backend.fullname" -}}
{{- printf "%s-backend" (include "cubebox.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cubebox.frontend.fullname" -}}
{{- printf "%s-frontend" (include "cubebox.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cubebox.backend.image" -}}
{{- $reg := .Values.image.registry -}}
{{- $repo := .Values.image.repository -}}
{{- $name := .Values.image.backend.name -}}
{{- $tag := required "image.backend.tag must be set (filled by build-and-push.sh into values.local.yaml)" .Values.image.backend.tag -}}
{{- printf "%s/%s/%s:%s" $reg $repo $name $tag -}}
{{- end -}}

{{- define "cubebox.frontend.image" -}}
{{- $reg := .Values.image.registry -}}
{{- $repo := .Values.image.repository -}}
{{- $name := .Values.image.frontend.name -}}
{{- $tag := required "image.frontend.tag must be set" .Values.image.frontend.tag -}}
{{- printf "%s/%s/%s:%s" $reg $repo $name $tag -}}
{{- end -}}

{{- define "cubebox.postgresql.host" -}}
{{- printf "%s-postgresql" .Release.Name -}}
{{- end -}}

{{- define "cubebox.redis.host" -}}
{{- printf "%s-redis-master" .Release.Name -}}
{{- end -}}

{{- define "cubebox.minio.host" -}}
{{- printf "%s-minio" .Release.Name -}}
{{- end -}}
