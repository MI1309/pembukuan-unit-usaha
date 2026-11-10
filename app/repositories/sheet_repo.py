import gspread
from oauth2client.service_account import ServiceAccountCredentials
from app.config import config
from app.core.utils import parse_amount

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
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

def get_sales_data():
    sheet = get_sheet("Rincian Penjualan", create=False)
    if not sheet: return []
    return sheet.get_all_records()

def get_all_records(sheet_name: str) -> list:
    sheet = get_sheet(sheet_name, create=False)
    return sheet.get_all_records() if sheet else []