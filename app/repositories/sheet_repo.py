import gspread
from oauth2client.service_account import ServiceAccountCredentials
from app.config import config
from app.core.utils import parse_amount

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(config.GOOGLE_CREDENTIALS_FILE, scope)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(config.SPREADSHEET_ID)

def get_sheet(name: str, create=True):
    try:
        return spreadsheet.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=name, rows=1000, cols=20) if create else None

def append_row(sheet_name: str, row: list):
    sheet = get_sheet(sheet_name)
    sheet.append_row(row)

def update_cell(sheet_name: str, row: int, column: int, value):
    """Update a worksheet cell using Google Sheets' 1-based coordinates."""
    sheet = get_sheet(sheet_name, create=False)
    if not sheet:
        raise ValueError(f"Worksheet not found: {sheet_name}")
    sheet.update_cell(row, column, value)

def delete_row(sheet_name: str, row: int):
    sheet = get_sheet(sheet_name, create=False)
    if not sheet:
        return
    sheet.delete_rows(row)

def get_drive_access_token() -> str:
    from app.services.drive_oauth import get_drive_credentials
    return get_drive_credentials().token

def get_sales_data():
    sheet = get_sheet("Rincian Penjualan", create=False)
    if not sheet: return []
    return sheet.get_all_records()

def get_all_records(sheet_name: str) -> list:
    sheet = get_sheet(sheet_name, create=False)
    return sheet.get_all_records() if sheet else []