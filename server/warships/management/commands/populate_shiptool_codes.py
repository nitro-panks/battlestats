"""Populate Ship.shiptool_code from the WoWS GameParams index.

Ship Tool (https://shiptool.st) addresses a ship in its URL via a short index
derived from the WoWS GameParams index string, e.g. ``PRSC110`` (Moskva) ->
``RC110`` -> ``https://shiptool.st/params?S=RC110``. The transform is exactly
Ship Tool's own ``createShortIndex``:

    index.match(/P([A-Z])S([A-Z])0*([0-9]+)$/) -> groups 1..3 joined

The GameParams index is not exposed by the WG public API, but WG's Vortex
encyclopedia returns it (in the ``name`` field, e.g. ``PRSC110_Pr_66_Moskva``)
keyed by the same numeric ship_id we store. This command fetches that catalog
once, joins on ship_id, derives the short code, and persists it on Ship.

Idempotent. Run on each WoWS patch that adds ships. Ships with no matching /
non-conforming index keep an empty code (the frontend then hides the link).
"""

import re

import requests
from django.core.management.base import BaseCommand

from warships.models import Ship


VORTEX_VEHICLES_URL = (
    'https://vortex.worldofwarships.com/api/encyclopedia/en/vehicles/'
)

# Mirror of Ship Tool's createShortIndex: P<nation>S<type><digits> at end of
# the GameParams index, leading zeros on the number stripped.
_SHORT_INDEX_RE = re.compile(r'P([A-Z])S([A-Z])0*([0-9]+)$')


def derive_shiptool_code(vortex_name: str) -> str:
    """Return the Ship Tool short code for a Vortex ``name`` (index prefix),
    or '' if the index does not conform to the expected pattern."""
    if not vortex_name:
        return ''
    index = vortex_name.split('_', 1)[0]
    match = _SHORT_INDEX_RE.match(index)
    if not match:
        return ''
    return ''.join(match.group(1, 2, 3))


class Command(BaseCommand):
    help = (
        'Populate Ship.shiptool_code from the WoWS GameParams index '
        '(sourced from WG Vortex). Idempotent; run on patches that add ships.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change without writing to the database.',
        )
        parser.add_argument(
            '--timeout',
            type=int,
            default=30,
            help='HTTP timeout (seconds) for the Vortex request.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        timeout = options['timeout']

        self.stdout.write('Fetching ship catalog from WG Vortex...')
        resp = requests.get(
            VORTEX_VEHICLES_URL,
            timeout=timeout,
            headers={'User-Agent': 'battlestats/shiptool-codes'},
        )
        resp.raise_for_status()
        vehicles = resp.json().get('data', {})
        if not vehicles:
            self.stderr.write(self.style.ERROR('Vortex returned no vehicles.'))
            return

        # ship_id (int) -> short code, for every conforming vehicle.
        code_by_id: dict[int, str] = {}
        for raw_id, entry in vehicles.items():
            code = derive_shiptool_code(entry.get('name') or '')
            if code:
                try:
                    code_by_id[int(raw_id)] = code
                except (TypeError, ValueError):
                    continue
        self.stdout.write(
            f'Vortex catalog: {len(vehicles)} vehicles, '
            f'{len(code_by_id)} with a derivable code.'
        )

        updated = 0
        unchanged = 0
        unmatched = []  # ships in our DB absent from Vortex / no code
        to_update = []
        for ship in Ship.objects.all().only('id', 'ship_id', 'name', 'shiptool_code'):
            # WG marks removed/test clone ships with a bracketed name
            # (e.g. "[Moskva]"); these aren't in Ship Tool's catalog, so a
            # link would dead-end. Leave them codeless (link hidden).
            if ship.name.startswith('['):
                unmatched.append(ship)
                continue
            code = code_by_id.get(ship.ship_id, '')
            if not code:
                unmatched.append(ship)
                # Leave an existing code in place rather than clobber on a
                # transient catalog gap; only fill blanks.
                continue
            if ship.shiptool_code == code:
                unchanged += 1
                continue
            ship.shiptool_code = code
            to_update.append(ship)
            updated += 1

        if to_update and not dry_run:
            Ship.objects.bulk_update(to_update, ['shiptool_code'], batch_size=500)

        self.stdout.write(self.style.SUCCESS(
            f'{"[dry-run] " if dry_run else ""}'
            f'updated={updated} unchanged={unchanged} '
            f'no_code={len(unmatched)} (link hidden for those).'
        ))
        if unmatched:
            sample = ', '.join(
                f'{s.ship_id}:{s.name}' for s in unmatched[:10]
            )
            self.stdout.write(f'  no-code sample: {sample}')
