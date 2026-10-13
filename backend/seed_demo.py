"""Run from the backend directory after loading the service DATABASE_URL env."""
import argparse
from datetime import date
import json
import sys


def main():
    parser = argparse.ArgumentParser(description="Add three fictional Delhi EventPilot scenarios without resetting existing data.")
    parser.add_argument("--reference-date", type=date.fromisoformat, metavar="YYYY-MM-DD",
                        help="Creation date for rolling scenarios; defaults to today's date in Asia/Kolkata")
    args = parser.parse_args()

    # Import only after parsing so --help works without database configuration.
    import main as app
    from demo_data import seed_demo_events, today_in_delhi
    from sqlalchemy.exc import SQLAlchemyError

    reference_date = args.reference_date or today_in_delhi()
    try:
        app.Base.metadata.create_all(app.engine)
        with app.Session() as db:
            events = seed_demo_events(db, app.DEMO_MODELS, reference_date)
        print(json.dumps({"reference_date": reference_date.isoformat(),
                          "created": sum(item["created"] for item in events),
                          "reused": sum(not item["created"] for item in events),
                          "events": events}, indent=2))
        return 0
    except SQLAlchemyError:
        print("Demo seeding failed; all new scenario changes were rolled back. Check database availability and permissions.", file=sys.stderr)
        return 1
    finally:
        app.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
