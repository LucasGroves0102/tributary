# secrets

Kubernetes Secrets for the server, committed encrypted with [SOPS](https://github.com/getsops/sops)
and [age](https://github.com/FiloSottile/age). Flux's `secrets` Kustomization
([deploy/flux](../flux)) decrypts them in the cluster with the age private key held in the
`sops-age` Secret, before any Helm release is applied.

| File | Secret | Namespace | Used by |
|---|---|---|---|
| `tributary.yaml` | `tributary` | tributary | the tributary chart (database URLs, JWT secret, Anthropic key, SEC User-Agent) |
| `ghcr-tributary.yaml`, `ghcr-platform.yaml`, `flux-ghcr.yaml` | `ghcr` | tributary, platform, flux-system | pulling images from GHCR; Flux image scanning |
| `platform.yaml` | `platform` | platform | the platform chart (Postgres password, OpenFGA datastore URI) |
| `monitoring-grafana.yaml` | `grafana-admin` | monitoring | Grafana admin login |
| `monitoring-discord.yaml` | `alertmanager-discord` | monitoring | Alertmanager webhook |
| `monitoring-postgres.yaml` | `postgres-exporter` | monitoring | postgres-exporter login |
| `flux-discord.yaml` | `discord-webhook` | flux-system | Flux deploy notifications |
| `cloudflared.yaml` | `tunnel-token` | cloudflared | Cloudflare Tunnel connector |
| `monitoring-kafka-exporter.yaml` (not yet created) | `kafka-exporter-kafka` | monitoring | kafka-exporter's Kafka SCRAM login (`scripts/kafka-passwords.sh`) |

Kafka SASL adds `kafka-password-*` keys to `platform.yaml` and `tributary.yaml`; which ones, and
the order to switch them on, are in the
[platform chart's README](../helm/platform/README.md#kafka-authentication-saslscram).

The keys each chart expects are listed in its README and `values.yaml`.

## Encryption

[`.sops.yaml`](../../.sops.yaml) encrypts every `deploy/secrets/*.yaml` to one age public key,
and only the values under `data` / `stringData`; names, namespaces and key names stay readable
in review. The matching private key lives only on the server (and in the owner's password
manager), never in this repository or in Terraform state.

Edit or add a secret on a machine with the private key:

```bash
sops deploy/secrets/tributary.yaml                         # opens decrypted in $EDITOR, re-encrypts on save
sops --encrypt --in-place deploy/secrets/new-secret.yaml   # encrypt a new plaintext file before committing
```

## Checks

`scripts/check-secrets-encrypted.py` fails if any file here has a value not starting with
`ENC[` or lacks SOPS metadata. It runs in CI and as a pre-commit hook (`make hooks`), next to a
gitleaks scan of every commit (`make secrets` scans the full history).
