{{- define "tributary.labels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/part-of: tributary
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Pod templates leave out helm.sh/chart: with Flux it changes on every commit, and a
changed pod template restarts the pod. */}}
{{- define "tributary.podLabels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/part-of: tributary
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end }}

{{- define "tributary.selector" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end }}

{{/* Pod labels the network policies select on, one per dependency. */}}
{{- define "tributary.uses" -}}
{{- $labels := dict }}
{{- range .uses }}
{{- if has . (list "sec" "anthropic") }}{{ $_ := set $labels "internet" "true" }}
{{- else if eq . "app-postgres" }}{{ $_ := set $labels "postgres" "true" }}
{{- else if has . (list "postgres" "kafka" "openfga") }}{{ $_ := set $labels . "true" }}
{{- end }}
{{- end }}
{{- range $k, $v := $labels }}
tributary.io/{{ $k }}: {{ $v | quote }}
{{- end }}
{{- end }}

{{/* The port a service serves /metrics on, or "" if it isn't scraped. Takes root and svc. */}}
{{- define "tributary.metricsPort" -}}
{{- if and .root.Values.metrics.enabled (or (not (hasKey .svc "metrics")) .svc.metrics) -}}
{{ .svc.port | default .root.Values.metrics.port }}
{{- end -}}
{{- end }}

{{- define "tributary.image" -}}
{{- $tag := required "image.tag is required (the commit SHA built by CI)" .root.Values.image.tag -}}
{{ .root.Values.image.registry }}/tributary-{{ .image }}:{{ $tag }}
{{- end }}

{{- define "tributary.secretEnv" -}}
- name: {{ .env }}
  valueFrom:
    secretKeyRef:
      name: {{ .root.Values.secretName }}
      key: {{ .key }}
{{- end }}

{{- define "tributary.env" -}}
{{- $root := .root -}}
- name: LOG_LEVEL
  value: {{ $root.Values.logLevel | quote }}
{{- range .uses }}
{{- if eq . "postgres" }}
{{ include "tributary.secretEnv" (dict "root" $root "env" "DATABASE_URL" "key" "database-url") }}
{{- else if eq . "app-postgres" }}
{{ include "tributary.secretEnv" (dict "root" $root "env" "APP_DATABASE_URL" "key" "app-database-url") }}
{{- else if eq . "kafka" }}
- name: KAFKA_BOOTSTRAP_SERVERS
  value: {{ $root.Values.kafka.bootstrap | quote }}
{{- else if eq . "openfga" }}
- name: FGA_API_URL
  value: {{ $root.Values.openfga.url | quote }}
{{- if $root.Values.openfga.auth.enabled }}
{{ include "tributary.secretEnv" (dict "root" $root "env" "FGA_API_TOKEN" "key" "openfga-api-token") }}
{{- end }}
{{- else if eq . "sec" }}
{{ include "tributary.secretEnv" (dict "root" $root "env" "SEC_USER_AGENT" "key" "sec-user-agent") }}
{{- else if eq . "anthropic" }}
{{ include "tributary.secretEnv" (dict "root" $root "env" "ANTHROPIC_API_KEY" "key" "anthropic-api-key") }}
{{- else if eq . "jwt" }}
{{ include "tributary.secretEnv" (dict "root" $root "env" "QUERY_API_JWT_SECRET" "key" "jwt-secret") }}
{{- else if eq . "query-api" }}
- name: QUERY_API_URL
  value: http://query-api:{{ index $root.Values.services "query-api" "port" }}
{{- else if and (eq . "demo") $root.Values.demo.enabled }}
- name: DEMO_USERS
  value: {{ $root.Values.demo.users | quote }}
{{- end }}
{{- end }}
{{- if and .metricsPort (not .port) }}
- name: METRICS_PORT
  value: {{ .metricsPort | quote }}
{{- end }}
{{- range $k, $v := .env }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end }}

{{/* Pod spec shared by Deployments, CronJobs and the migration Job. */}}
{{- define "tributary.podSpec" -}}
{{- $root := .root -}}
automountServiceAccountToken: false
enableServiceLinks: false
{{- with $root.Values.image.pullSecret }}
imagePullSecrets:
  - name: {{ . }}
{{- end }}
securityContext:
  runAsNonRoot: true
  seccompProfile:
    type: RuntimeDefault
{{- with .restartPolicy }}
restartPolicy: {{ . }}
{{- end }}
containers:
  - name: {{ .name }}
    image: {{ include "tributary.image" (dict "root" $root "image" .image) }}
    imagePullPolicy: {{ $root.Values.image.pullPolicy }}
    {{- with .command }}
    command: {{ toJson . }}
    {{- end }}
    env:
      {{- include "tributary.env" . | nindent 6 }}
    {{- with .port }}
    ports:
      - name: http
        containerPort: {{ . }}
    readinessProbe:
      httpGet:
        path: {{ $.probePath | default "/health" }}
        port: http
      periodSeconds: 5
    livenessProbe:
      httpGet:
        path: {{ $.probePath | default "/health" }}
        port: http
      periodSeconds: 10
      failureThreshold: 3
    {{- end }}
    {{- if and .metricsPort (not .port) }}
    ports:
      - name: metrics
        containerPort: {{ .metricsPort }}
    {{- end }}
    resources:
      {{- toYaml (.resources | default $root.Values.resources) | nindent 6 }}
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop: [ALL]
    volumeMounts:
      - name: tmp
        mountPath: /tmp
volumes:
  - name: tmp
    emptyDir:
      sizeLimit: 64Mi
{{- end }}
