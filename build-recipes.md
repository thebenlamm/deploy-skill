# Build-on-box recipes (native, no Docker)

Pick by the analyzer's detected stack. Builds run as `ubuntu` on a fresh Ubuntu
24.04 Lightsail box, extracted to `/srv/<APP>` (never `~/<APP>` — the app
service itself runs as a dedicated `app` user, and `/srv` avoids 0750-home
traversal problems for Caddy). **Ship source by tar-piping the operator's
local clone** (the box has no GitHub creds for private repos):
```
ssh -i <KEY.pem> ubuntu@$IP 'sudo mkdir -p /srv/<APP> && sudo chown ubuntu:ubuntu /srv/<APP>'
tar czf - -C <LOCAL_CLONE> --exclude='./.git' --exclude='./.env*' \
  --exclude='./*.pem' --exclude='./secrets*' --exclude='./.aws' \
  --exclude='./node_modules' . \
  | ssh -i <KEY.pem> ubuntu@$IP 'rm -rf /srv/<APP>/* && tar xzf - -C /srv/<APP>'
```
For tracked-files-only shipping (cleaner, no denylist needed), use
`git archive HEAD | ssh -i <KEY.pem> ubuntu@$IP 'tar xf - -C /srv/<APP>'` instead.

Then SSH in, install the toolchain, build, wire the systemd `ExecStart` + `WorkingDirectory`.
Run long builds with `run_in_background: true` and tee to `~/provision.log`.

Common base: `sudo apt-get update -y && sudo apt-get install -y git curl ca-certificates fail2ban unattended-upgrades`.

---

## static (S3 alternative: just serve via Caddy)
If analyzer says `s3-cloudfront`, prefer S3+CloudFront. But on a box you already
have, simplest is Caddy serving the build dir. Never `file_server` the app
ROOT — serve only the build output dir:
```
cd /srv/<APP> && npm ci && npm run build      # output dist/ or build/
```
Caddyfile: `root * /srv/<APP>/dist` + `file_server` instead of reverse_proxy.

## node (server: express/fastify/nest/sveltekit-node)
```
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs
cd /srv/<APP> && npm ci && npm run build --if-present
```
Run: `ExecStart=/usr/bin/node <entry>` (e.g. `build` for SvelteKit node adapter,
or `dist/main.js`, `server.js`). `WorkingDirectory` = `/srv/<APP>` or `/srv/<APP>/frontend`.

## python (flask/fastapi/django)
```
sudo apt-get install -y python3 python3-venv python3-pip
cd /srv/<APP> && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
```
Run: `ExecStart=/srv/<APP>/.venv/bin/gunicorn -b 127.0.0.1:3000 <module>:app`
(FastAPI: `uvicorn <module>:app --port 3000`).

## java (maven / spring)
```
sudo apt-get install -y openjdk-17-jdk maven      # match the version in pom.xml
cd /srv/<APP> && mvn -q -DskipTests package        # or install
```
Run: `ExecStart=/usr/bin/java -Xmx<heap>g -jar /srv/<APP>/target/<artifact>.jar`.
Pin `-Xmx` to box RAM − 2GB.

## node + jvm polyglot (the proven Parshandata shape)
Install BOTH toolchains, build Java modules then the frontend, run the Node entrypoint
which spawns the JVM worker:
```
sudo apt-get install -y openjdk-17-jdk maven
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs
cd /srv/<APP> && mvn -q -DskipTests install
cd /srv/<APP>/frontend-new && npm ci && <DEPLOY_TARGET_ENV> npm run build
```
Run: `ExecStart=/usr/bin/node build`, `WorkingDirectory=/srv/<APP>/frontend-new`.
Mirror every runtime `ENV` from the Dockerfile into the systemd unit (classes dirs,
texts dir, `*_JAVA_OPTS=-Xmx4g`, admin token from `secrets.env`).

---

## After any recipe
1. `sudo systemctl daemon-reload && sudo systemctl enable --now <APP>`
2. `sudo systemctl status <APP>` — confirm `active (running)` + "Listening on …".
3. Install Caddy (provisioning.md §4), then verify `curl https://<host>/healthz`.
4. Run any documented warmup (e.g. `POST /api/.../warmup`) to absorb cold start.

## If the build fails
- Unresolved import / missing file → likely the wrong branch. Ask which branch is
  deployable; re-tar from that branch's clone. (Don't stub the author's code without asking.)
- OOM during build (big data / JVM) → check `free -h`; the box may be undersized vs the
  analyzer's RAM estimate, or the build needs more than runtime (regen steps).
- Case-sensitive import works on Mac, fails on Linux → real filename casing differs.
