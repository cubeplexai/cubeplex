{{/* vim: set filetype=mustache: */}}
{{- define "cubeplex.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cubeplex.fullname" -}}
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

{{- define "cubeplex.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "cubeplex.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "cubeplex.backend.fullname" -}}
{{- printf "%s-backend" (include "cubeplex.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cubeplex.frontend.fullname" -}}
{{- printf "%s-frontend" (include "cubeplex.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cubeplex.backend.image" -}}
{{- $reg := .Values.image.registry -}}
{{- $repo := .Values.image.repository -}}
{{- $name := .Values.image.backend.name -}}
{{- $tag := required "image.backend.tag must be set (filled by build-and-push.sh into values.local.yaml)" .Values.image.backend.tag -}}
{{- printf "%s/%s/%s:%s" $reg $repo $name $tag -}}
{{- end -}}

{{- define "cubeplex.frontend.image" -}}
{{- $reg := .Values.image.registry -}}
{{- $repo := .Values.image.repository -}}
{{- $name := .Values.image.frontend.name -}}
{{- $tag := required "image.frontend.tag must be set" .Values.image.frontend.tag -}}
{{- printf "%s/%s/%s:%s" $reg $repo $name $tag -}}
{{- end -}}

{{- define "cubeplex.postgresql.host" -}}
{{- printf "%s-postgresql" .Release.Name -}}
{{- end -}}

{{- define "cubeplex.redis.host" -}}
{{- printf "%s-redis-master" .Release.Name -}}
{{- end -}}

{{- define "cubeplex.rustfs.host" -}}
{{- printf "%s-rustfs" .Release.Name -}}
{{- end -}}

{{- define "cubeplex.docling.host" -}}
{{- printf "%s-docling" .Release.Name -}}
{{- end -}}
