# Security notice

**VideoFeed is designed for a single user on a trusted local network.**
There is no built-in authentication, authorization, or rate-limiting.
The API is publicly accessible to anyone who can reach the listening
port.

## Do

- Run on `127.0.0.1` (the default) if you only need local access.
- If you want to reach it from other devices on your LAN, bind to a
  LAN IP and put it behind a firewall that blocks inbound WAN traffic.
- If you need remote access, put it behind a reverse proxy that adds
  authentication (Caddy, nginx + Basic Auth, Cloudflare Access, Tailscale,
  `ssh -L` tunnel — anything that isn't "exposed to the open internet").

## Don't

- **Do not expose the default HTTP port (7999) directly to the
  public internet.** Any HTTP client on the planet can then delete
  rows, recycle files, enqueue unbounded transcoding jobs, or list
  your filesystem via debug endpoints.

## Reporting issues

If you find a vulnerability, please open a private security advisory
on GitHub or email the maintainer rather than filing a public issue.

## Fixed in recent releases

- **0.2.0** — Path traversal in `/api/stream/{id}/hls/{path}`; unsafe
  CORS default (`allow_origins="*" + allow_credentials=true`); unknown
  `/api/...` routes returning 200 via the SPA catch-all.
