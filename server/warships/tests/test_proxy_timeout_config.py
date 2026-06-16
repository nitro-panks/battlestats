"""Regression guard: the cold-path 502 hardening (latency runbook Tier 2a) must
stay present in the deploy templates.

These three proxy/request timeouts were silently reverted twice as collateral
inside unrelated bundled commits (most recently ``bcfe232``), each time leaving
prod's cold-lookup path back on nginx/gunicorn implicit defaults with no clean
502 backstop. There is no runtime contract to unit-test here — the settings live
in config templates that ship at deploy time — so this is a lightweight
file-content presence check: cheap insurance that a future bundled commit can't
remove them a third time without turning a test red.

Settings are matched as substrings (quote/indentation/comment-agnostic) so a
legitimate reword of the surrounding comment or a quote-style change does not
trip the guard — only an actual removal of the functional setting does.
"""

from pathlib import Path

# tests/ -> warships/ -> server/ -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[3]

GUNICORN_CONF = REPO_ROOT / "server" / "gunicorn.conf.py"
DEV_NGINX_CONF = REPO_ROOT / "server" / "nginx.conf"
PROD_NGINX_TEMPLATE = REPO_ROOT / "client" / "deploy" / "bootstrap_droplet.sh"


def test_gunicorn_request_timeout_present():
    """Primary 502 fix: gunicorn recycles a wedged worker on a tunable timeout
    (default 25s) instead of running on the implicit 30s default."""
    text = GUNICORN_CONF.read_text()
    assert "GUNICORN_TIMEOUT_SECONDS" in text, (
        "gunicorn request timeout reverted from server/gunicorn.conf.py "
        "(latency runbook Tier 2a, primary 502 fix)"
    )


def test_dev_nginx_proxy_timeouts_present():
    """Dev/Docker edge parity so the gap isn't re-discovered locally."""
    text = DEV_NGINX_CONF.read_text()
    assert "proxy_connect_timeout 5s" in text, (
        "proxy_connect_timeout reverted from server/nginx.conf /api/ block"
    )
    assert "proxy_read_timeout 20s" in text, (
        "proxy_read_timeout reverted from server/nginx.conf /api/ block"
    )


def test_prod_nginx_template_proxy_timeouts_present():
    """Prod nginx template (re-provision source of truth) keeps the /api/
    connect-stall hardening."""
    text = PROD_NGINX_TEMPLATE.read_text()
    assert "proxy_connect_timeout 5s" in text, (
        "proxy_connect_timeout reverted from client/deploy/bootstrap_droplet.sh "
        "/api/ block"
    )
    assert "proxy_read_timeout 20s" in text, (
        "proxy_read_timeout reverted from client/deploy/bootstrap_droplet.sh "
        "/api/ block"
    )
