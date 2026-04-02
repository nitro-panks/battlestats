from warships.tasks import update_clan_tier_distribution_task
from warships.models import Clan
import os
import sys
import django
from datetime import timedelta
from django.utils.timezone import now

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'battlestats.settings')
django.setup()


def run():
    print("Starting background fill of clan tier metrics via Celery...")
    thirty_days_ago = now() - timedelta(days=30)
    active_clans = Clan.objects.filter(
        last_fetch__gte=thirty_days_ago).values_list('clan_id', flat=True)

    count = active_clans.count()
    print(f"Found {count} clans refreshed in the last 30 days. Dispatching...")

    for clan_id in active_clans:
        update_clan_tier_distribution_task.delay(str(clan_id))

    print(f"Dispatched {count} background tasks successfully.")


if __name__ == "__main__":
    run()
