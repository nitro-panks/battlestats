#!/usr/bin/env python3
"""Daily battlestats ops-digest email.

Runs unattended on the production droplet via cron (11:30 UTC). Reads the three
durable benchmark snapshot families the /observation, /crawl-yield and
/recapture skills read, selects the correct comparison points in Python (so the
LLM never miscomputes a delta), asks the Anthropic API to synthesize a morning
digest under the skills' own verdict-discipline rules, and emails it.

Self-contained: stdlib only (urllib + smtplib), no venv, no pip installs. Config
and secrets come from an env file (default /etc/battlestats-ops-email.env, chmod
600), NEVER from this script -- it lives in a public repo.

Fail-loud: any error still sends an email (subject tagged FAILED) carrying the
traceback and whatever raw data was read, then exits non-zero for the cron log.

Flags:
  --dry-run   Build the digest and print the rendered email to stdout; do not send.
  --no-llm    Skip the Anthropic call; email a plain deterministic table (also the
              automatic fallback if the API errors under normal runs).
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
import traceback
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

DEFAULT_ENV_FILE = "/etc/battlestats-ops-email.env"
DEFAULT_BENCH_DIR = "/opt/battlestats-server/shared/benchmarks"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

REALMS = ("na", "eu", "asia")


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def load_env_file(path: str) -> None:
    """Merge KEY=VALUE lines from an env file into os.environ (env wins if set)."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# --------------------------------------------------------------------------- #
# snapshot loading
# --------------------------------------------------------------------------- #
def _parse_ts(s: str) -> datetime:
    # captured_at looks like 2026-07-01T04:30:04.188264 (naive, UTC by convention)
    return datetime.fromisoformat(s)


def _load_dir(bench_dir: str, sub: str) -> list[dict]:
    d = Path(bench_dir) / sub
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            obj = json.loads(f.read_text())
            obj["_file"] = f.name
            obj["_ts"] = _parse_ts(obj["captured_at"])
            out.append(obj)
        except Exception:
            continue
    out.sort(key=lambda o: o["_ts"])
    return out


def _closest(snaps: list[dict], target: datetime, lo_h: float, hi_h: float):
    """Snapshot whose ts is closest to `target`, within [lo_h, hi_h] hours away."""
    best, best_gap = None, None
    for s in snaps:
        gap_h = abs((s["_ts"] - target).total_seconds()) / 3600.0
        if lo_h <= gap_h <= hi_h and (best_gap is None or gap_h < best_gap):
            best, best_gap = s, gap_h
    return best


OBS_FIELDS = (
    "active_1d", "active_7d", "distinct_productive", "coverage_ratio_vs_7d",
    "productive_rate", "fresh_within_24h", "fresh_frac", "stale_over_24h",
    "obs_bulk_floor", "obs_poll", "never_observed",
)


def _obs_scope(node: dict) -> dict:
    return {k: node.get(k) for k in OBS_FIELDS}


def _delta(cur: dict, prev: dict | None) -> dict:
    if not prev:
        return {}
    out = {}
    for k in OBS_FIELDS:
        a, b = cur.get(k), prev.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out[k] = round(a - b, 4)
    return out


def gather_observation(bench_dir: str) -> dict:
    snaps = _load_dir(bench_dir, "observation-floor")
    if not snaps:
        return {"available": 0}
    L = snaps[-1]
    d1 = _closest(snaps[:-1], L["_ts"] - timedelta(hours=24), 20, 28)
    d7 = _closest(snaps[:-1], L["_ts"] - timedelta(days=7), 24 * 6, 24 * 8)

    def block(s: dict):
        return {
            "captured_at": s["captured_at"],
            "totals": _obs_scope(s.get("totals", {})),
            "realms": {r: _obs_scope(s.get("realms", {}).get(r, {})) for r in REALMS},
        }

    result = {
        "available": len(snaps),
        "config": L.get("config", {}),
        "latest": block(L),
        "d1": block(d1) if d1 else None,
        "d7": block(d7) if d7 else None,
    }
    if d1:
        result["delta_vs_d1"] = {
            "totals": _delta(L.get("totals", {}), d1.get("totals", {})),
            "realms": {
                r: _delta(L.get("realms", {}).get(r, {}), d1.get("realms", {}).get(r, {}))
                for r in REALMS
            },
        }
    return result


def gather_crawl_yield(bench_dir: str) -> dict:
    snaps = _load_dir(bench_dir, "crawl-yield")
    if not snaps:
        return {"available": 0}
    by_realm: dict[str, list[dict]] = {r: [] for r in REALMS}
    for s in snaps:
        r = s.get("realm")
        if r in by_realm:
            by_realm[r].append(s)

    def scope(s: dict):
        return {
            "captured_at": s.get("captured_at"),
            "pass_started_at": s.get("pass_started_at"),
            "players_classified": s.get("players_classified"),
            "buckets": s.get("buckets", {}),
            "yield_total": s.get("yield_total"),
            "overlap_total": s.get("overlap_total"),
            "yield_frac": s.get("yield_frac"),
            "overlap_frac": s.get("overlap_frac"),
        }

    out = {"available": len(snaps), "realms": {}}
    for r in REALMS:
        lst = by_realm[r]
        if not lst:
            out["realms"][r] = None
            continue
        out["realms"][r] = {
            "latest": scope(lst[-1]),
            "prev": scope(lst[-2]) if len(lst) > 1 else None,
        }
    return out


RECAP_FIELDS = (
    "mode", "band_days", "limit", "scanned", "wg_calls", "no_data", "hidden",
    "chunk_errors", "still_dormant", "advanced", "yield_frac", "into7d",
    "into7d_clanned", "into7d_clanless", "still_lapsed", "still_lapsed_clanless",
    "cursor_stamped",
)


def gather_recapture(bench_dir: str) -> dict:
    snaps = _load_dir(bench_dir, "recapture-lapsed")
    if not snaps:
        return {"available": 0}
    by_realm: dict[str, list[dict]] = {r: [] for r in REALMS}
    for s in snaps:
        r = s.get("realm")
        if r in by_realm:
            by_realm[r].append(s)

    def scope(s: dict):
        d = {k: s.get(k) for k in RECAP_FIELDS}
        d["captured_at"] = s.get("captured_at")
        return d

    out = {"available": len(snaps), "realms": {}}
    for r in REALMS:
        lst = by_realm[r]
        out["realms"][r] = scope(lst[-1]) if lst else None
    return out


# --------------------------------------------------------------------------- #
# synthesis
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You are the analyst writing a battlestats.online operations \
morning digest. battlestats is a World of Warships player/clan stats platform. \
You are given machine-selected, pre-diffed snapshot data from three independent \
nightly instruments. Write a concise, warm, precise HTML email (voice: Data from \
Star Trek -- analytical, no hype, no emdashes; use colons/semicolons). Lead with \
a 2-3 sentence "what matters today" summary, then a short section per instrument.

CRITICAL interpretation discipline (these instruments are noisy; do NOT cry \
regression):

OBSERVATION FLOOR -- measures the battle-observation sweep over active-7d players.
- Headline is coverage_ratio_vs_7d = distinct_productive / active_7d. Its \
realistic CEILING is the daily-active fraction active_1d/active_7d (~25-45%), \
because a player who did not battle in the window cannot produce an event. \
Report cov/7d both raw AND as a % of that ceiling.
- Decompose every coverage move: did distinct_productive change (real capture \
shift) or did active_7d change (denominator shift)? Say which.
- stale_over_24h is MOSTLY the change-gate "non-mover wall" -- by design, not a \
backlog. A large/steady value is expected. Only a rising stale WITH falling \
distinct_productive means cadence is slipping.
- NA productive_rate runs below EU/ASIA: known, not a regression.
- Day-to-day variance at fixed config is large. A single down day is noise, NOT \
a regression. Only flag a regression if sustained across multiple clean days AND \
distinct_productive is down while active_7d is flat. Otherwise say "within noise."

CRAWL YIELD -- measures the clan crawl's floor-impossible value: net-new \
discovery + dormant->active re-detection.
- yield_total = discovered_active + reactivated (floor-impossible; the point). \
overlap_total = refreshed_active (the floor already covers these).
- Verdict "saturated / trim cadence" requires BOTH low yield_frac AND low \
discovered_dormant. A low yield_frac with high discovered_dormant means the \
universe is still growing (seed corn for future reactivations): do NOT call that \
saturated. Per-pass counts vary; need >=2-3 same-realm passes before any verdict.
- Passes are per-realm and lagged (a pass runs many hours). Only compare a realm \
against itself.

RECAPTURE -- the cheap daily bulk account/info sweep of the dormant pool.
- advanced = returners found (last_battle_time moved past our stored value). \
into7d = returned inside active-7d (floor harvests them free next cycle). \
into7d_clanless = THE marginal value: returners nothing else recovers (the crawl \
only walks clan rosters). LEAD the recapture section with into7d_clanless.
- A healthy dormant pool is mostly still_dormant, so low single-digit % yield is \
EXPECTED and fine; judge absolute returner count, not the rate.
- mode=detect means writes are off (measuring, not recapturing) -- flag it. High \
errors/no_data = WG trouble. scanned << band = cursor exhausted the pool \
(maintenance steady state, fine).

Output STRICT JSON only, no prose outside it, no markdown fences: \
{"subject": "...", "html_body": "..."}. Subject <=78 chars, start it with \
"[battlestats] ". html_body is a complete <html>...</html> fragment using inline \
styles, readable on mobile, no external images."""


def call_anthropic(model: str, api_key: str, data_package: dict) -> dict:
    body = {
        "model": model,
        "max_tokens": 6000,
        # This is deterministic formatting, not a reasoning task. Extended thinking
        # is default-on for these models and will burn the whole token budget on a
        # thinking block, returning no text (stop_reason=max_tokens). Disable it.
        "thinking": {"type": "disabled"},
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Here is today's machine-selected snapshot data. Deltas are "
                    "pre-computed (delta_vs_d1 = latest minus the ~24h-prior "
                    "snapshot). Write the digest.\n\n"
                    + json.dumps(data_package, indent=2, default=str)
                ),
            }
        ],
    }
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    text = "".join(
        blk.get("text", "") for blk in payload.get("content", []) if blk.get("type") == "text"
    ).strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"): text.rfind("}") + 1]
    parsed = json.loads(text)
    return {"subject": parsed["subject"], "html_body": parsed["html_body"]}


# --------------------------------------------------------------------------- #
# deterministic fallback rendering (no LLM)
# --------------------------------------------------------------------------- #
def render_plain(data: dict) -> dict:
    lines = ["battlestats ops digest (deterministic fallback -- no LLM synthesis)\n"]

    obs = data["observation"]
    lines.append("== Observation floor ==")
    if obs.get("available"):
        t = obs["latest"]["totals"]
        a7, dp = t.get("active_7d"), t.get("distinct_productive")
        a1 = t.get("active_1d")
        cov = t.get("coverage_ratio_vs_7d")
        ceil = (a1 / a7) if (a1 and a7) else None
        lines.append(f"  captured_at: {obs['latest']['captured_at']}")
        lines.append(
            f"  TOTAL active_7d={a7} distinct_productive={dp} "
            f"cov/7d={cov} ceiling(a1/a7)={round(ceil,4) if ceil else 'n/a'}"
        )
        for r in REALMS:
            rr = obs["latest"]["realms"][r]
            lines.append(
                f"    {r}: active_7d={rr.get('active_7d')} "
                f"productive={rr.get('distinct_productive')} cov/7d={rr.get('coverage_ratio_vs_7d')}"
            )
    else:
        lines.append("  (no snapshots)")

    cy = data["crawl_yield"]
    lines.append("\n== Crawl yield ==")
    if cy.get("available"):
        for r in REALMS:
            node = cy["realms"].get(r)
            if not node:
                lines.append(f"  {r}: (no pass)")
                continue
            l = node["latest"]
            lines.append(
                f"  {r}: {l['captured_at']} classified={l['players_classified']} "
                f"yield={l['yield_total']}({l['yield_frac']}) overlap={l['overlap_total']}({l['overlap_frac']}) "
                f"buckets={l['buckets']}"
            )
    else:
        lines.append("  (no snapshots)")

    rc = data["recapture"]
    lines.append("\n== Recapture ==")
    if rc.get("available"):
        for r in REALMS:
            node = rc["realms"].get(r)
            if not node:
                lines.append(f"  {r}: (no run)")
                continue
            lines.append(
                f"  {r}: {node['captured_at']} mode={node['mode']} scanned={node['scanned']} "
                f"advanced={node['advanced']}({node['yield_frac']}) into7d={node['into7d']} "
                f"into7d_clanless={node['into7d_clanless']} still_lapsed={node['still_lapsed']}"
            )
    else:
        lines.append("  (no snapshots)")

    text = "\n".join(lines)
    html = "<html><body><pre style='font:13px/1.4 monospace'>" + _esc(text) + "</pre></body></html>"
    return {"subject": "[battlestats] daily ops digest (fallback)", "html_body": html, "text": text}


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
# email
# --------------------------------------------------------------------------- #
def send_email(subject: str, html_body: str, text_body: str) -> None:
    host = cfg("SMTP_HOST", "smtp.purelymail.com")
    port = int(cfg("SMTP_PORT", "465"))
    user = cfg("SMTP_USER")
    pw = cfg("SMTP_PASS")
    mail_from = cfg("MAIL_FROM", user)
    mail_to = cfg("MAIL_TO", "august.schlubach@gmail.com")
    if not (user and pw and mail_from and mail_to):
        raise RuntimeError("SMTP_USER/SMTP_PASS/MAIL_FROM/MAIL_TO must all be set")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg.set_content(text_body or "See the HTML version of this message.")
    msg.add_alternative(html_body, subtype="html")

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30, context=ctx) as s:
            s.login(user, pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, pw)
            s.send_message(msg)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args
    no_llm = "--no-llm" in args

    load_env_file(cfg("OPS_EMAIL_ENV_FILE", DEFAULT_ENV_FILE))
    bench_dir = cfg("BENCH_DIR", DEFAULT_BENCH_DIR)

    data = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "observation": gather_observation(bench_dir),
        "crawl_yield": gather_crawl_yield(bench_dir),
        "recapture": gather_recapture(bench_dir),
    }

    # Choose rendering path.
    email = None
    llm_error = None
    if not no_llm:
        api_key = cfg("ANTHROPIC_API_KEY")
        model = cfg("ANTHROPIC_MODEL", "claude-sonnet-5")
        if not api_key:
            llm_error = "ANTHROPIC_API_KEY not set"
        else:
            try:
                out = call_anthropic(model, api_key, data)
                email = {"subject": out["subject"], "html_body": out["html_body"], "text": ""}
            except Exception as e:
                detail = e
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        detail = f"{e} :: {e.read().decode('utf-8')[:500]}"
                    except Exception:
                        detail = str(e)
                llm_error = f"{type(e).__name__}: {detail}"

    if email is None:
        # deterministic fallback (either --no-llm, no key, or API failed)
        email = render_plain(data)
        if llm_error:
            note = f"<p style='color:#b00'>LLM synthesis failed, sent deterministic fallback: {_esc(llm_error)}</p>"
            email["html_body"] = email["html_body"].replace("<body>", "<body>" + note)
            email["subject"] = "[battlestats] daily ops digest (fallback: LLM error)"

    if dry_run:
        print("SUBJECT:", email["subject"])
        print("---- HTML ----")
        print(email["html_body"])
        if llm_error:
            print("---- LLM ERROR ----\n", llm_error)
        return 0

    send_email(email["subject"], email["html_body"], email.get("text", ""))
    print(f"[ok] sent: {email['subject']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        tb = traceback.format_exc()
        sys.stderr.write(tb + "\n")
        # fail loud: still try to send a failure email
        try:
            body = "<html><body><h2>battlestats daily ops email FAILED</h2><pre>" + \
                _esc(tb) + "</pre></body></html>"
            send_email("[battlestats] daily ops email FAILED", body, tb)
            print("[warn] sent failure notification email")
        except Exception:
            sys.stderr.write("could not send failure email:\n" + traceback.format_exc() + "\n")
        sys.exit(1)
