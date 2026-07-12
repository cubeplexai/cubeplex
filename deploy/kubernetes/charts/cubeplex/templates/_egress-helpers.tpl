{{/*
egress helpers — bootstrap the long-lived CA + child certs.

Strategy: a triplet of Secrets (mitm-ca, webhook-tls, backend-mtls) are
either ALL looked up from a previous render, or ALL regenerated in this
render. Helm's `genSignedCert` only accepts the `sprig.certificate`
struct returned by `genCA`, which can't be reconstructed from PEM; so we
can't lookup the CA alone and mint fresh childs from it.

Operational consequence:
  * Normal upgrades: lookup finds all three → no rotation.
  * `helm.sh/resource-policy: keep` on the CA Secret keeps CA across
    `helm uninstall` (so a fresh re-install can find it).
  * If anyone manually deletes one of the three TLS Secrets, the next
    upgrade re-derives all three (CA rotates too). That's intentional —
    a half-deleted triplet would otherwise stall.
*/}}

{{- define "cubeplex.egress.fullname" -}}
{{- printf "%s-egress" (include "cubeplex.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cubeplex.egress.webhookFullname" -}}
{{- printf "%s-egress-webhook" (include "cubeplex.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cubeplex.egress.webhook.image" -}}
{{- $reg := .Values.egress.webhook.image.registry | default .Values.image.registry -}}
{{- $repo := .Values.egress.webhook.image.repository | default .Values.image.repository -}}
{{- $name := .Values.egress.webhook.image.name -}}
{{- $tag := required "egress.webhook.image.tag must be set in values.local.yaml when egress.enabled" .Values.egress.webhook.image.tag -}}
{{- printf "%s/%s/%s:%s" $reg $repo $name $tag -}}
{{- end -}}

{{/*
Compute (or reuse) the CA + child certs as a single triplet.

Returns a JSON-encoded dict with the six PEM strings the consuming
templates need:
  CACert        CAKey
  WebhookCertPem  WebhookKeyPem
  ServerCertPem   ServerKeyPem
*/}}
{{- define "cubeplex.egress.certs" -}}
{{- $ns := .Release.Namespace -}}
{{- $caName := printf "%s-mitm-ca" (include "cubeplex.egress.fullname" .) -}}
{{- $whName := printf "%s-tls" (include "cubeplex.egress.webhookFullname" .) -}}
{{- $bName  := printf "%s-mtls" (include "cubeplex.backend.fullname" .) -}}

{{- $existingCA := lookup "v1" "Secret" $ns $caName -}}
{{- $existingWH := lookup "v1" "Secret" $ns $whName -}}
{{- $existingB  := lookup "v1" "Secret" $ns $bName -}}

{{- $caCertPem := "" -}}
{{- $caKeyPem  := "" -}}
{{- $whCertPem := "" -}}
{{- $whKeyPem  := "" -}}
{{- $bCertPem  := "" -}}
{{- $bKeyPem   := "" -}}

{{- if and $existingCA $existingWH $existingB -}}
  {{- $caCertPem = index $existingCA.data "mitmproxy-ca-cert.pem" | b64dec -}}
  {{- $caKeyPem  = index $existingCA.data "mitmproxy-ca.pem"      | b64dec -}}
  {{- $whCertPem = index $existingWH.data "tls.crt" | b64dec -}}
  {{- $whKeyPem  = index $existingWH.data "tls.key" | b64dec -}}
  {{- $bCertPem  = index $existingB.data  "tls.crt" | b64dec -}}
  {{- $bKeyPem   = index $existingB.data  "tls.key" | b64dec -}}
{{- else -}}
  {{- $ca := genCA "cubeplex-egress-mitm-ca" 3650 -}}
  {{- $whDns := list
      (printf "%s.%s.svc" (include "cubeplex.egress.webhookFullname" .) $ns)
      (printf "%s.%s.svc.cluster.local" (include "cubeplex.egress.webhookFullname" .) $ns)
  -}}
  {{- $wh := genSignedCert (printf "%s.%s.svc" (include "cubeplex.egress.webhookFullname" .) $ns) nil $whDns 365 $ca -}}
  {{- $bDns := list
      (printf "%s.%s.svc" (include "cubeplex.backend.fullname" .) $ns)
      (printf "%s.%s.svc.cluster.local" (include "cubeplex.backend.fullname" .) $ns)
      "egress-exchange.cubeplex.internal"
  -}}
  {{- $b := genSignedCert "cubeplex-egress-exchange" nil $bDns 365 $ca -}}

  {{- $caCertPem = $ca.Cert -}}
  {{- $caKeyPem  = $ca.Key  -}}
  {{- $whCertPem = $wh.Cert -}}
  {{- $whKeyPem  = $wh.Key  -}}
  {{- $bCertPem  = $b.Cert  -}}
  {{- $bKeyPem   = $b.Key   -}}
{{- end -}}

{{- dict
    "CACert"          $caCertPem
    "CAKey"           $caKeyPem
    "WebhookCertPem"  $whCertPem
    "WebhookKeyPem"   $whKeyPem
    "ServerCertPem"   $bCertPem
    "ServerKeyPem"    $bKeyPem
  | toJson -}}
{{- end -}}
