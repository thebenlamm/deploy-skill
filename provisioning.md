# Provisioning reference — Lightsail VM (local `aws --profile`)

All commands run **locally** in Bash with `--profile <PROFILE>` (assumes into the
target sub-account). Set once: `export AWS_PROFILE=<PROFILE> REGION=us-east-1`.
Placeholders: `<APP>` instance name, `<PROFILE>`, `<SIZE-BUNDLE>` from analyzer.

VM bundle ids (Linux, ~$/mo): `nano_3_0` $5 · `micro_3_0` $7 · `small_3_0` $12 ·
`medium_3_0` $24 · `large_3_0` $44 (8GB) · `xlarge_3_0` $84 (16GB). Use the
`*_ipv6_*` variants ONLY if you don't need a public IPv4 (you usually do).

## 0. Confirm the profile (before spending)
```
aws sts get-caller-identity --profile <PROFILE>   # must show the target account
aws lightsail get-bundles --region $REGION --query "bundles[?ramSizeInGb==\`8\`]"
```

## 1. Key + instance
`$D` holds RUNTIME ARTIFACTS ONLY (keys, secrets.env, state.json) — gitignored.
The friction LOG is a separate, committed file: `deploys/NNN-<app>.md`.
```
D=./deploys/NNN-<app>; mkdir -p "$D"   # runtime artifacts dir, not the log
printf '*.pem\n*.key\nsecrets.env\nkeyerr.txt\n' > "$D/.gitignore"

aws lightsail create-key-pair --region $REGION --key-pair-name <APP>-key \
  --query privateKeyBase64 --output text > "$D/<APP>-key.pem" && chmod 600 "$D/<APP>-key.pem"

aws lightsail create-instances --region $REGION --instance-names <APP> \
  --availability-zone ${REGION}a --blueprint-id ubuntu_24_04 \
  --bundle-id <SIZE-BUNDLE> --key-pair-name <APP>-key \
  --tags key=managed-by,value=deploy-concierge

# Static IP: without it the address changes on stop/start, killing the
# sslip.io URL + cert.
aws lightsail allocate-static-ip --region $REGION --static-ip-name <APP>-ip
aws lightsail attach-static-ip --region $REGION --static-ip-name <APP>-ip \
  --instance-name <APP>
```

## 2. Wait → open ports → get IP
```
until [ "$(aws lightsail get-instance-state --region $REGION --instance-name <APP> \
  --query state.name --output text)" = running ]; do sleep 6; done

# Port 22 restricted to the operator's current IP; re-run this line if your IP
# changes. 80/443 stay open to the world.
aws lightsail put-instance-public-ports --region $REGION --instance-name <APP> \
  --port-infos fromPort=22,toPort=22,protocol=TCP,cidrs=$(curl -s ifconfig.me)/32 \
               fromPort=80,toPort=80,protocol=TCP \
               fromPort=443,toPort=443,protocol=TCP

IP=$(aws lightsail get-static-ip --region $REGION --static-ip-name <APP>-ip \
     --query staticIp.ipAddress --output text); echo "$IP"
```
**Carry `$IP` forward.** Do NOT read it from instance metadata on-box — Lightsail
NAT returns empty `public-ipv4`, which silently breaks the Caddy host.

At the end of this step, record state (fill in values already in shell vars;
`HOST` is the same value §4 computes from `$IP`):
```
HOST="${IP//./-}.sslip.io"
cat > "$D/state.json" <<EOF
{
  "app": "<APP>", "profile": "<PROFILE>", "region": "$REGION",
  "bundle": "<SIZE-BUNDLE>", "ip": "$IP", "host": "$HOST",
  "url": "https://${HOST}", "monthly_usd": <BUNDLE_\$/MO>,
  "created": "$(date -u +%FT%TZ)"
}
EOF
```

## 3. Secrets, then the systemd unit
`ubuntu` has passwordless sudo — the app runs as a dedicated `app` user instead,
so app RCE ≠ root:
```
sudo useradd -r -s /usr/sbin/nologin app || true
```

Secrets never go in the (world-readable) unit file. Transfer + install them first:
```
scp -i <KEY.pem> secrets.env ubuntu@$IP:/tmp/
ssh -i <KEY.pem> ubuntu@$IP 'sudo install -d -m750 /etc/<APP> \
  && sudo install -m600 -o root -g root /tmp/secrets.env /etc/<APP>/secrets.env \
  && rm /tmp/secrets.env'
```

Write to `/etc/systemd/system/<APP>.service`. `WorkingDirectory` = the app's
run dir under `/srv/<APP>` (never `~` — systemd doesn't expand it), `ExecStart`
= the run command from the stack recipe. `Environment=` lines are for
non-secrets only (PORT etc.) — secrets load via `EnvironmentFile=`. Mirror
every runtime `ENV` from the Dockerfile. Pin JVM heap: `-Xmx` ≈ box RAM − 2GB.
```
[Unit]
After=network.target
[Service]
Type=simple
User=app
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/srv/<APP>
# narrow further to the app's own data dir if it only writes e.g. /srv/<APP>/data
WorkingDirectory=<RUN_DIR>          # e.g. /srv/<APP> or /srv/<APP>/frontend
Environment=PORT=3000
# ...one Environment= line per non-secret runtime var...
EnvironmentFile=-/etc/<APP>/secrets.env
ExecStart=<RUN_CMD>
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
```
`sudo systemctl daemon-reload && sudo systemctl enable --now <APP>`

## 4. Caddy auto-TLS (correct keyring path — the #1 install failure)
```
sudo install -d -m0755 /etc/apt/keyrings
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
  | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update -y && sudo apt-get install -y caddy

HOST="${IP//./-}.sslip.io"          # IP carried from step 2, NOT from metadata
printf '%s {\n    reverse_proxy 127.0.0.1:3000\n}\n' "$HOST" | sudo tee /etc/caddy/Caddyfile
sudo systemctl restart caddy
echo "PUBLIC_URL=https://${HOST}"
```
The `signed-by=` in `debian.deb.txt` points at
`/usr/share/keyrings/caddy-stable-archive-keyring.gpg` — the key MUST be there.

## 5. Verify (real request, not a port check)
```
curl -s https://${HOST}/healthz   # or / ; then run any documented warmup POST
```

## Teardown (Lightsail instances delete freely — no Org close-account limit)
```
aws lightsail delete-instance --region $REGION --instance-name <APP>
# A detached static IP still bills ~$3.50/mo — release it too:
aws lightsail release-static-ip --region $REGION --static-ip-name <APP>-ip
aws lightsail delete-key-pair --region $REGION --key-pair-name <APP>-key
rm -f "$D/<APP>-key.pem"   # stale private keys on the laptop are a liability
```

## Real domain (optional, later)
Point an A record at `$IP`, set the Caddyfile host to the domain, restart Caddy
(it gets a cert automatically). No CloudFront needed.
