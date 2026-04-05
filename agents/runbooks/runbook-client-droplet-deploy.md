# Client Droplet Deploy Runbook

This runbook sets up the Next.js client on a bare Ubuntu DigitalOcean droplet without changing the local Docker-based development flow.

## Why this path

- Keep local development exactly where it is today.
- Use a simple push-to-deploy flow from the repo when you want to publish client changes.
- Avoid introducing GitHub Actions or a full CI/CD pipeline before there is real operational pressure for it.

## Production shape

- Nginx listens on port 80 on the droplet.
- Nginx proxies `/api/*` directly to the Django backend on `127.0.0.1:8888` when the backend is deployed on the same droplet.
- Nginx proxies the remaining requests to the Next.js app on `127.0.0.1:3001`.
- The Next.js app still has a `BATTLESTATS_API_ORIGIN` rewrite for non-droplet environments, so local development outside Docker keeps working.
- A systemd unit keeps the client process alive.
- Releases are deployed into `/opt/battlestats-client/releases/<timestamp>` and `current` is switched after a successful build.

## One-time bootstrap

From the repo root:

```bash
chmod +x client/deploy/bootstrap_droplet.sh client/deploy/deploy_to_droplet.sh
NGINX_SERVER_NAME="battlestats.online www.battlestats.online" \
API_ORIGIN=http://127.0.0.1:8888 \
./client/deploy/bootstrap_droplet.sh YOUR_DROPLET_IP
```

What the bootstrap does:

- installs Nginx, rsync, and Node.js 20 on the droplet
- creates the `battlestats` system user
- creates `/etc/battlestats-client.env`
- installs the `battlestats-client` systemd unit
- installs the Nginx site and enables it

If your Django backend is not on the same droplet, set `API_ORIGIN` to the actual backend origin before bootstrapping.

If you want both the apex domain and `www` host to resolve on the droplet, point both DNS records at the droplet IP and include both names in `NGINX_SERVER_NAME`.

## First deploy and later updates

When local client changes are ready to publish:

```bash
./client/deploy/deploy_to_droplet.sh YOUR_DROPLET_IP
```

That command:

- rsyncs the `client/` directory to a new release directory on the droplet
- runs `npm ci`
- runs `npm run build`
- flips `/opt/battlestats-client/current` to the new release via atomic-and-verified activation
- restarts `battlestats-client`
- verifies the active client release target and `battlestats-client` plus `nginx` via the shared post-deploy wrapper

For deploy-time verification and cross-service follow-up after backend or client rollout, use `runbook-post-deploy-post-bounce-operations-2026-04-05.md` as the canonical checklist.

If the rollout also requires deploy-scoped smoke checks or backend follow-up warms, run them explicitly from the repo root:

```bash
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP smoke --base-url http://127.0.0.1:8888
```

## Runtime config

The droplet reads runtime variables from `/etc/battlestats-client.env`.

Current supported values:

```env
BATTLESTATS_API_ORIGIN=http://127.0.0.1:8888
BATTLESTATS_APP_ORIGIN=https://battlestats.online
NEXT_PUBLIC_GA_MEASUREMENT_ID=
```

Set `BATTLESTATS_APP_ORIGIN` to the public canonical site origin so generated `robots.txt`, `sitemap.xml`, and `metadataBase` all agree with the domain users and crawlers actually hit.

After editing that file on the droplet:

```bash
systemctl restart battlestats-client
```

## Service checks

Useful remote commands:

```bash
ssh root@YOUR_DROPLET_IP 'systemctl status battlestats-client --no-pager'
ssh root@YOUR_DROPLET_IP 'journalctl -u battlestats-client -n 100 --no-pager'
ssh root@YOUR_DROPLET_IP 'nginx -t && systemctl status nginx --no-pager'
```

## SSL later

This bootstrap uses plain HTTP on port 80 so the client is reachable immediately after DNS is pointed at the droplet IP.

When you are ready to attach a domain, add TLS with Certbot or another reverse-proxy/TLS layer. That is the point where automation via GitHub Actions becomes more reasonable, but it is not required for the first deployment.

## Agentic Insights & Known Issues: Deployment Resource Starvation

**Critical context for future AI agents diagnosing production incidents or modifying the deployment pipeline:**

- **The Outage Vector:** As seen in `client/deploy/deploy_to_droplet.sh`, the remote execution block runs `npm ci` and `npm run build` _natively on the production droplet_ via SSH.
- **Resource Exhaustion:** Next.js production builds (Turbopack, page prerendering, multiple worker threads) are extremely CPU and memory intensive.
- **Symptom:** Because this build shares the exact same hardware resources as the live PM2 / Node.js instance and existing Django backend services, it frequently starves the live server of CPU time. This results in the client application becoming completely unresponsive (e.g., timeouts, 502/504 errors) for several minutes while the deployment script runs.
- **Agent Directives:**
  1. **Incident Triage:** If a user reports "the site is down or not responding" immediately during or after a deployment, check if the Next.js `build` process is currently running. The site will typically self-heal once the build completes and the PM2 service is restarted.
  2. **Pipeline Evolution:** If tasked with improving deployment reliability or achieving zero-downtime deployments, the first step is to offload the build step from the droplet. Refactor the deployment pipeline to build the `.next` artifacts locally or via a CI runner (like GitHub Actions), and only `rsync` the compiled static assets and build outputs to the droplet. This entirely eliminates deployment-triggered resource starvation.

## Agentic Insights & Known Issues: React Hooks & Celery Hydration Polling

**Critical context for future AI agents handling new features with delayed asynchronous Celery hydration pipelines on production:**

- **The Outage Vector:** During the rollout of the Clan Tiers Distribution graph feature, the Next.js `X-Clan-Tiers-Pending` custom HTTP polling mechanism suffered from parallel infinite loading and 0-value states.
- **Root Cause 1 (React Component Architecture):** A race condition occurred because both the wrapper `<ClanTierDistributionContainer>` and the internal child `<ClanTierSVG>` each instantiated the exact same `useClanTiersDistribution(clanId)` React hook. When the polling finished globally, the wrapper correctly destroyed the `"Aggregating..."` loading screen, but the inner SVG re-rendered with empty `[]` state triggers, wiping off the graph entirely. Check for drill-down prop delegation instead of double-instantiating parallel UI data-fetching hooks.
- **Root Cause 2 (Celery Data Pipeline & Caching):** When iterating sub-relations (e.g. `Player` records for a generic `Clan`), be extremely careful of the DB returning 0-value sums because the `tiers_json` blob hasn't actually been requested locally by a specific user yet. Our initial `cache.set` implementation naively wrote these 0-value empty datasets directly into `Redis` for 24 hours without checking if hydration was actually necessary.
- **Agent Directives:**
  1. **Strict Recursion over Foreign Keys:** Always evaluate the actual entity blobs (e.g. `tiers_json == null`) and recursively push missing relations to Celery `_delay_task_safely` prior to assembling the final summary object.
  2. **Refusal to Cache Pending States:** If even one entity inside a collective Clan scope needs hydration, refuse to resolve the cache and violently return `[]` to force the UI into the `X-**-Pending` state until it clears.
  3. **High-Timeout E2E Polling Checks:** Any automation testing async Celery pipelines *must* use high timeouts (e.g., `60000ms` vs `30000ms`) inside Playwright `toBeVisible()` assertions to allow sufficient cold-start backpropagation across production droplets before falsely assuming the rendering failed.
