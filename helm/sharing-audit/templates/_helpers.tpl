{{/*
Return the name of the secret to use.
If existingSecret is set, use that; otherwise use the chart-managed secret.
*/}}
{{- define "sharing-audit.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
  {{- .Values.secrets.existingSecret -}}
{{- else -}}
  {{- .Release.Name }}-secrets
{{- end -}}
{{- end -}}
