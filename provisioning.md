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
```
D=./deploys/NNN-<app>; mkdir -p "$D"   # or wherever you keep deploy records
printf '*.pem\n*.key\nsecrets.env\nkeyerr.txt\n' > "$D/.gitignore"

aws lightsail create-key-pair --region $REGION --key-pair-name <APP>-key \
  --query privateKeyBase64 --output text > "$D/<APP>-key.pem" && chmod 600 "$D/<APP>-key.pem"

aws lightsail create-instances --region $REGION --instance-names <APP> \
  --availability-zone ${REGION}a --blueprint-id ubuntu_24_04 \
  --bundle-id <SIZE-BUNDLE> --key-pair-name <APP>-key \
  --tags key=managed-by,value=deploy-concierge
```

## 2. Wait → open ports → get IP
```
until [ "$(aws lightsail get-instance-state --region $REGION --instance-name <APP> \
  --query state.name --output text)" = running ]; do sleep 6; done

aws lightsail put-instance-public-ports --region $REGION --instance-name <APP> \
  --port-infos fromPort=22,toPort=22,protocol=TCP fromPort=80,toPort=80,protocol=TCP \
               fromPort=443,toPort=443,protocol=TCP

IP=$(aws lightsail get-instance --region $REGION --instance-name <APP> \
     --query instance.publicIpAddress --output text); echo "$IP"
```
**Carry `$IP` forward.** Do NOT read it from instance metadata on-box — Lightsail
NAT returns empty `public-ipv4`, which silently breaks the Caddy host.

## 3. systemd unit (template — fill ENV from the app's Dockerfile/spec)
Write to `/etc/systemd/system/<APP>.service`. `User=ubuntu`, `WorkingDirectory`
= the app's run dir, `ExecStart` = the run command from the stack recipe.
Mirror every runtime `ENV` from the Dockerfile. Pin JVM heap: `-Xmx` ≈ box RAM − 2GB.
```
[Unit]
After=network.target
[Service]
Type=simple
User=ubuntu
WorkingDirectory=<RUN_DIR>
Environment=PORT=3000
# ...one Environment= line per runtime env var...
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
aws lightsail delete-key-pair --region $REGION --key-pair-name <APP>-key
```

## Real domain (optional, later)
Point an A record at `$IP`, set the Caddyfile host to the domain, restart Caddy
(it gets a cert automatically). No CloudFront needed.
