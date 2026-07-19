# Local Odoo 16 test environment

Runs Odoo **16.0** from the source clone at `/Users/taj/personal/odoo` against a
PostgreSQL container, with this repository mounted so the `mcp_connector_gateway`
module is installable and its test suite runnable.

Version 16 is used because our first client for this connector is on Odoo 16.

## Files
- `../docker-compose.yml` — `db` (postgres:15) + `odoo` services.
- `odoo.conf` — DB connection + `addons_path` (`/odoo/addons`, `/mnt/extra-addons`).

## Modules
- `mcp_connector_gateway` — **Odoo 16** target (our first client). Uses 16-compatible
  view syntax (`<tree>`, `attrs="{...}"`, `//div[hasclass('settings')]`, `fa-` icons).
- `mcp_connector_gateway_v18` — copy targeting **Odoo 17/18** (`<list>`, `invisible=`,
  `//block[@name='integration']`, `oi-` icons). Test it against a 17/18 runtime
  separately. The two are never installed together (separate versions/DBs).

The `odoo` service uses the official multi-arch **`odoo:16.0`** image purely for
its prebuilt dependencies (Python 3.10, gevent, wkhtmltopdf, ...), then runs
**our bind-mounted source clone** via `python3 /odoo/odoo-bin`. So the code that
actually executes is the tree at `/Users/taj/personal/odoo` — nothing is compiled
from source, avoiding the old-gevent build failure. Host edits to the clone or
this repo apply on container restart.

## Prerequisites
Docker is available via Colima, but the Compose plugin may be missing. Install it:

```bash
brew install docker-compose
mkdir -p ~/.docker/cli-plugins
ln -sfn "$(brew --prefix)/bin/docker-compose" ~/.docker/cli-plugins/docker-compose
docker compose version   # verify
```

Colima shares the home directory over virtiofs, so `/Users/taj/personal/...`
bind mounts work out of the box.

## Usage
From the repository root (`/Users/taj/personal/odoo_mcp`):

```bash
# Pull the base image (first time only)
docker compose pull odoo

# First run: create the DB and install the module
docker compose run --rm odoo \
  python3 /odoo/odoo-bin -c /etc/odoo/odoo.conf \
  -d odoo -i mcp_connector_gateway --stop-after-init

# Start Odoo (http://localhost:8069)
docker compose up -d

# Run the gateway test suite
docker compose run --rm odoo \
  python3 /odoo/odoo-bin -c /etc/odoo/odoo.conf \
  -d odoo -u mcp_connector_gateway \
  --test-enable --test-tags mcp_connector_gateway --stop-after-init

# Tail logs / stop
docker compose logs -f odoo
docker compose down            # keep data
docker compose down -v         # also drop DB + filestore volumes
```

The pure-Python service-token test needs no Odoo runtime:

```bash
python mcp_connector_gateway/tests/test_service_token.py
```
