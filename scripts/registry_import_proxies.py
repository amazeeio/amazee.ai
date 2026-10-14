#!/usr/bin/env python3
"""One-time import of live proxy deployments into the model registry.

Usage: python scripts/registry_import_proxies.py [REGION_NAME ...]
With no names it imports every active region that is not imported yet.
A region that was imported before is refused.
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import sessionmaker

from app.db.database import engine
from app.db.models import DBRegion
from app.registry.importer import AlreadyImported, import_proxy
from app.registry.models import DBRegistryProxy

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("regions", nargs="*", help="region names; default: all not imported")
    args = parser.parse_args()

    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    failed = False
    try:
        query = db.query(DBRegion).filter(DBRegion.is_active.is_(True))
        if args.regions:
            query = query.filter(DBRegion.name.in_(args.regions))
        else:
            imported = db.query(DBRegistryProxy.region_id).filter(
                DBRegistryProxy.imported_at.isnot(None)
            )
            query = query.filter(DBRegion.id.notin_(imported))
        regions = query.order_by(DBRegion.id).all()
        missing = set(args.regions) - {r.name for r in regions}
        if missing:
            logger.error("No active region named: %s", ", ".join(sorted(missing)))
            failed = True
        for region in regions:
            try:
                logger.info("Imported %s: %s", region.name, import_proxy(db, region))
            except AlreadyImported as e:
                logger.error("%s", e)
                failed = True
            except Exception:
                logger.exception("Import of %s failed", region.name)
                failed = True
    finally:
        db.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
