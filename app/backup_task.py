import sys

from app.config import config
from app.services.backup_service import daily_backup


def main() -> int:
    if not config.BACKUP_ENABLED:
        print("Backup disabled by configuration.")
        return 0
    result = daily_backup()
    if result.get("status") != "success":
        print(f"Daily backup failed: {result.get('error', 'unknown error')}")
        return 1
    print(f"Daily backup created: {result.get('name')}; pruned={result.get('deleted', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
