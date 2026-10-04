# deploy/ — running Marcel on the NUC

Docker Compose for the hub, systemd user units for the runner and redeploys, and the Cloudflare
tunnel ingress. Secrets live in `deploy/.env.local`, which is never committed. Filled in by F14.
