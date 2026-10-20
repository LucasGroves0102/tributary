# terraform

Rebuilds what sits under Flux on a server that already runs k3s and the host Postgres
(`deploy/host`). Two root modules with separate credentials:

| Module | Manages | Credentials |
|---|---|---|
| `cluster` | Flux components and the sync to this repository; Flux then applies `deploy/flux` | kubeconfig |
| `cloudflare` | Tunnel, its route, the DNS record, HTTPS settings, the rate-limit rule | `CLOUDFLARE_API_TOKEN` |

State stays local (`terraform.tfstate`, git-ignored). Neither module reads a private key: the
two bootstrap secrets are created on the server, where the keys live.

## Fresh server

```bash
# on the server: repository deploy key (added to GitHub with write access) and the age key
kubectl create namespace flux-system
kubectl -n flux-system create secret generic flux-system \
  --from-file=identity=deploy-key --from-file=identity.pub=deploy-key.pub \
  --from-file=known_hosts=github-known-hosts
kubectl -n flux-system create secret generic sops-age \
  --from-file=age.agekey=$HOME/.config/sops/age/keys.txt

# anywhere with the kubeconfig
terraform -chdir=deploy/terraform/cluster init
terraform -chdir=deploy/terraform/cluster apply

export CLOUDFLARE_API_TOKEN=...   # Zone DNS/Settings/WAF edit, Account Cloudflare Tunnel edit
terraform -chdir=deploy/terraform/cloudflare init
terraform -chdir=deploy/terraform/cloudflare apply
```

`cluster` refuses to apply until both secrets exist. A new tunnel needs its token in
`deploy/secrets/cloudflared.yaml`:
`terraform -chdir=deploy/terraform/cloudflare output -raw tunnel_id`, then copy the token from
the tunnel's page in Cloudflare and encrypt it with sops.

## Adopting resources created in the dashboard

```bash
cd deploy/terraform/cloudflare
terraform import cloudflare_zero_trust_tunnel_cloudflared.server <account_id>/<tunnel_id>
terraform import cloudflare_zero_trust_tunnel_cloudflared_config.server <account_id>/<tunnel_id>
terraform import cloudflare_dns_record.app <zone_id>/<record_id>
terraform import cloudflare_zone_setting.always_use_https <zone_id>/always_use_https
terraform import cloudflare_zone_setting.min_tls_version <zone_id>/min_tls_version
terraform import cloudflare_ruleset.rate_limit zones/<zone_id>/<ruleset_id>
terraform plan   # should show no changes
```
