{{- define "platform.labels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/part-of: tributary-platform
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Pod templates leave out helm.sh/chart: with Flux it changes on every commit, and a
changed pod template restarts the pod. */}}
{{- define "platform.podLabels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/part-of: tributary-platform
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end }}

{{- define "platform.selector" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end }}

{{- define "platform.setupImage" -}}
{{ .Values.images.setup.repository }}:{{ required "images.setup.tag is required (the commit SHA built by CI)" .Values.images.setup.tag }}
{{- end }}

{{- define "platform.podSecurity" -}}
automountServiceAccountToken: false
enableServiceLinks: false
securityContext:
  runAsNonRoot: true
  runAsUser: {{ .uid }}
  runAsGroup: {{ .uid }}
  fsGroup: {{ .uid }}
  seccompProfile:
    type: RuntimeDefault
{{- end }}

{{- define "platform.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: [ALL]
{{- end }}

{{- define "platform.postgresPassword" -}}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secretName }}
      key: postgres-password
{{- end }}

{{- define "platform.datastore" -}}
- name: OPENFGA_DATASTORE_ENGINE
  value: postgres
- name: OPENFGA_DATASTORE_URI
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secretName }}
      key: openfga-datastore-uri
{{- end }}
