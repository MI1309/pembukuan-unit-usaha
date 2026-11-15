import argparse
import sys

from app.repositories import sheet_repo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verifikasi atau pulihkan transaksi dari arsip.")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--manifest", help="Verifikasi manifest arsip berdasarkan key")
    actions.add_argument("--restore", help="Pulihkan ledger dari manifest arsip berdasarkan key")
    parser.add_argument("--apply", action="store_true", help="Terapkan restore; default hanya dry-run")
    args = parser.parse_args(argv)
    if args.apply and not args.restore:
        parser.error("--apply hanya dapat digunakan bersama --restore")

    try:
        if args.manifest:
            results = sheet_repo.verify_archive_manifest(args.manifest)
            valid = all(result["valid"] for result in results.values())
            for unit, result in results.items():
                print(f"{unit!r}: {'OK' if result['valid'] else 'MISMATCH'}; count={result['count']}")
            return 0 if valid else 1

        counts = sheet_repo.rollback_archive(args.restore, apply=args.apply)
        print("MODE: APPLY" if args.apply else "MODE: DRY RUN (tidak ada perubahan)")
        for unit, count in counts.items():
            print(f"{unit!r}: {count} baris {'dipulihkan' if args.apply else 'akan dipulihkan'}")
        print(f"Total: {sum(counts.values())} baris")
        return 0
    except Exception as error:
        print(f"Operasi arsip gagal: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())