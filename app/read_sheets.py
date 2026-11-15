import argparse
import sys

from app.repositories import sheet_repo


def read_sheets(sheet_name: str | None = None) -> int:
    spreadsheet = sheet_repo.get_spreadsheet()
    print(f"Spreadsheet: {spreadsheet.title}")
    worksheets = spreadsheet.worksheets()
    if sheet_name:
        worksheets = [worksheet for worksheet in worksheets if worksheet.title == sheet_name]
        if not worksheets:
            print(f"Worksheet tidak ditemukan: {sheet_name}", file=sys.stderr)
            return 2

    for worksheet in worksheets:
        header_row = max(1, worksheet.frozen_row_count)
        read_through = max(1, header_row)
        values = worksheet.get(f"1:{read_through}")
        first_row = values[:1]
        first_cell = str(first_row[0][0]).strip() if first_row and first_row[0] else ""
        if first_cell.lower().startswith("kakorlap unit usaha"):
            if header_row < 2:
                header_row = 2
                values += worksheet.get("2:2")
        print(f"\n[{worksheet.title}]")
        print(f"Tab ID: {worksheet.id}; posisi: {worksheet.index}")
        print(f"Grid: {worksheet.row_count} baris x {worksheet.col_count} kolom")
        print(
            f"Freeze: {worksheet.frozen_row_count} baris, "
            f"{worksheet.frozen_col_count} kolom; "
            f"tersembunyi: {'ya' if worksheet.isSheetHidden else 'tidak'}; "
            f"gridlines: {'tersembunyi' if worksheet.is_gridlines_hidden else 'tampil'}"
        )
        if not values:
            print(f"Header: (tidak ada pada baris 1-{header_row})")
            continue

        if header_row > 1 and first_cell:
            print(f"Judul: {first_cell}")
        header_index = header_row - 1
        if len(values) <= header_index or not values[header_index]:
            print(f"Header: (tidak ditemukan pada baris {header_row})")
            continue
        headers = [str(value).strip() or f"Kolom {index + 1}" for index, value in enumerate(values[header_index])]
        print(f"Header (baris {header_row}, {len(headers)} kolom): " + " | ".join(headers))

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Baca struktur worksheet pada spreadsheet dari .env tanpa membaca data transaksi.")
    parser.add_argument("--sheet", help="Nama worksheet tertentu; tanpa opsi ini semua tab dibaca")
    args = parser.parse_args(argv)

    try:
        return read_sheets(args.sheet)
    except Exception as error:
        print(f"Gagal membaca spreadsheet: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())