"""Real daily workflows require an explicit observation archive in their workspace."""
import shutil
from hydro_database import PUBLIC


def seed_daily_observations(manager):
    public = manager.runtime_root / 'test-observations'
    for relative in ('spatial/stations.json', 'observations/catalog.json',
                     'observations/rain/2017.json', 'observations/river/2017.json'):
        target = public / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PUBLIC / relative, target)
    manager.database.import_existing(public, public / 'no-extra-events.json')
