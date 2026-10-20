# flux

GitOps for the server (cluster `hermes`), Flux v2.9.6. Flux watches `main` of this repository
and applies everything under `deploy/`; a merge to `main` is a deploy.

| Path | Applied by | Contents |
|---|---|---|
| `clusters/hermes/flux-system/` | Terraform `cluster` module (bootstrap), then Flux | Flux components (`gotk-components.yaml`, generated, do not edit) and the GitRepository + root Kustomization (`gotk-sync.yaml`) |
| `clusters/hermes/stack.yaml` | root Kustomization | the Kustomizations below, in dependency order |
| `namespaces/` | `namespaces` | `tributary`, `platform`, `monitoring`, `cloudflared` |
| [`../secrets/`](../secrets) | `secrets` | SOPS-encrypted Secrets, decrypted with the age key in Secret `sops-age` |
| `releases/` | `releases` | HelmReleases for [platform](../helm/platform), [tributary](../helm/tributary) (after platform) and [monitoring](../helm/monitoring), charts from this repository |
| `images/` | `images` | image automation: picks the newest `main-<run>-<sha>` tag and commits it to `releases/` |
| `notifications/` | `notifications` | Discord messages for HelmRelease, Kustomization and image-update events |
| `cloudflared/` | `cloudflared` | Cloudflare Tunnel connector (2 replicas, outbound only) and its NetworkPolicies |

## Deploy flow

1. CI builds every image on a push to `main`, and when all jobs pass tags each one
   `main-<run number>-<sha>` (`.github/workflows/ci.yml`, `release` job).
2. The `ImagePolicy` picks the highest run number; `ImageUpdateAutomation` rewrites the tag
   setters in `releases/*.yaml` and pushes a "Deploy new images [skip ci]" commit to `main`.
3. Flux reconciles the HelmReleases; failed upgrades are retried and rolled back
   (`remediation.retries: 3`).

Every deploy is therefore a commit that can be reverted. Pull before pushing: the bot commits
to `main`.

CI validates all of these manifests with kubeconform.
