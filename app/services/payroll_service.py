import re
from datetime import datetime

from app.config import config
from app.core.utils import format_rupiah, now_tz, parse_amount
from app.repositories import sheet_repo
from app.services import karyawan_service

PAYROLL_SHEET = "Slip Gaji"
ADJUSTMENT_SHEET = "Penyesuaian Gaji"
PAYROLL_FIELDS = [
    "NIK", "Nama", "Periode", "Hadir", "Terlambat", "Izin/Sakit", "Cuti", "Alpha",
    "Gaji Pokok", "Tunjangan", "Potongan Alpha", "Potongan Telat", "Gaji Bersih",
    "Dicetak", "Lembur", "Bonus", "Potongan Lain",
]
ADJUSTMENT_FIELDS = [
    "Tanggal", "NIK", "Nama", "Periode", "Jenis", "Jumlah (Rp)",
    "Keterangan", "Dicatat Oleh",
]


def _period(period: str | None = None) -> str:
    value = period or now_tz().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise ValueError("Format periode salah. Gunakan yyyy-mm.")
    return value


def _ensure_sheet(name: str, headers: list[str]) -> None:
    worksheet = sheet_repo.get_sheet(name)
    if not worksheet.get_all_values():
        sheet_repo.append_row(name, headers)


def _adjustments(nik: str, period: str) -> tuple[int, int]:
    bonus = deductions = 0
    target_nik = karyawan_service.normalize_nik(nik)
    for row in sheet_repo.get_all_records(ADJUSTMENT_SHEET):
        if (karyawan_service.normalize_nik(row.get("NIK")) != target_nik
                or str(row.get("Periode", "")).strip() != period):
            continue
        amount = parse_amount(row.get("Jumlah (Rp)", row.get("Jumlah", 0)))
        if str(row.get("Jenis", "")).strip().lower() == "bonus":
            bonus += amount
        else:
            deductions += amount
    return bonus, deductions


def calculate_payroll(employee: dict, period: str | None = None,
                      attendance_records: list[dict] | None = None,
                      adjustment_records: list[dict] | None = None) -> dict:
    period = _period(period)
    stats = karyawan_service.attendance_stats(employee, period, attendance_records)
    bonus = other_deductions = 0
    adjustments = adjustment_records
    if adjustments is None:
        adjustments = sheet_repo.get_all_records(ADJUSTMENT_SHEET)
    for row in adjustments:
        if (karyawan_service.normalize_nik(row.get("NIK")) != employee["nik"]
                or str(row.get("Periode", "")).strip() != period):
            continue
        amount = parse_amount(row.get("Jumlah (Rp)", row.get("Jumlah", 0)))
        if str(row.get("Jenis", "")).strip().lower() == "bonus":
            bonus += amount
        else:
            other_deductions += amount

    salary = parse_amount(employee.get("Gaji Pokok", employee.get("gaji", 0)))
    allowance = round(salary * config.TUNJANGAN_PERSEN / 100)
    alpha_deduction = round(salary / config.GAJI_HARI_KERJA) * stats["alpha"]
    late_deduction = stats["terlambat"] * config.POTONGAN_TELAT
    overtime_pay = stats["lemburJam"] * config.LEMBUR_PER_JAM
    net_salary = max(0, salary + allowance + overtime_pay + bonus
                     - alpha_deduction - late_deduction - other_deductions)
    return {
        "NIK": employee["nik"],
        "Nama": employee["nama"],
        "Periode": period,
        "Hadir": stats["hadir"],
        "Terlambat": stats["terlambat"],
        "Izin/Sakit": stats["izin"] + stats["sakit"],
        "Cuti": stats["cuti"],
        "Alpha": stats["alpha"],
        "Gaji Pokok": salary,
        "Tunjangan": allowance,
        "Potongan Alpha": alpha_deduction,
        "Potongan Telat": late_deduction,
        "Gaji Bersih": net_salary,
        "Dicetak": now_tz(),
        "Lembur": overtime_pay,
        "Bonus": bonus,
        "Potongan Lain": other_deductions,
        "Statistik": stats,
    }


def _save_slip(result: dict) -> None:
    _ensure_sheet(PAYROLL_SHEET, PAYROLL_FIELDS)
    records = sheet_repo.get_all_records(PAYROLL_SHEET)
    row_number = None
    for index, record in enumerate(records, start=2):
        if (karyawan_service.normalize_nik(record.get("NIK")) == result["NIK"]
                and str(record.get("Periode", "")).strip() == result["Periode"]):
            row_number = index
    values = [result[field] for field in PAYROLL_FIELDS]
    if row_number is None:
        sheet_repo.append_row(PAYROLL_SHEET, values)
    else:
        for column, value in enumerate(values, start=1):
            sheet_repo.update_cell(PAYROLL_SHEET, row_number, column, value)


def _slip_text(employee: dict, result: dict) -> str:
    stats = result["Statistik"]
    lines = [
        "💵 *SLIP GAJI KARYAWAN*", f"_{config.COMPANY}_", "━━━━━━━━━━━━━━━━━━",
        f"👤 Nama: *{employee['nama']}*", f"🆔 NIK: {employee['nik']}",
        f"💼 Jabatan: {employee['jabatan']}", f"🏢 Departemen: {employee['departemen']}",
        f"📅 Periode: *{karyawan_service._period_label(result['Periode'])}*",
        "━━━━━━━━━━━━━━━━━━", "📊 *PRESENSI:*", f"✅ Hadir: {stats['hadir']} hari",
        f"⏰ Terlambat: {stats['terlambat']}x", f"🏥 Izin/Sakit: {stats['izin'] + stats['sakit']} hari",
        f"🌴 Cuti: {stats['cuti']} hari",
        f"❌ Alpha: {stats['alpha']} hari" + (f" ({stats['alphaOtomatis']} tanpa catatan)" if stats["alphaOtomatis"] else ""),
    ]
    if stats["lemburJam"]:
        lines.append(f"⏱️ Lembur: {stats['lemburJam']} jam")
    lines += ["━━━━━━━━━━━━━━━━━━", "💰 *RINCIAN GAJI:*",
              f"• Gaji Pokok      : {format_rupiah(result['Gaji Pokok'])}",
              f"• Tunjangan ({config.TUNJANGAN_PERSEN}%) : {format_rupiah(result['Tunjangan'])}"]
    if result["Lembur"]:
        lines.append(f"• Lembur          : {format_rupiah(result['Lembur'])}")
    if result["Bonus"]:
        lines.append(f"• Bonus           : {format_rupiah(result['Bonus'])}")
    lines += [f"• Potongan Alpha  : -{format_rupiah(result['Potongan Alpha'])}",
              f"• Potongan Telat  : -{format_rupiah(result['Potongan Telat'])}"]
    if result["Potongan Lain"]:
        lines.append(f"• Potongan Lain   : -{format_rupiah(result['Potongan Lain'])}")
    lines += ["━━━━━━━━━━━━━━━━━━", "✅ *GAJI BERSIH:*", f"💵 *{format_rupiah(result['Gaji Bersih'])}*",
              "━━━━━━━━━━━━━━━━━━", f"_Dicetak: {now_tz().strftime('%d %B %Y')}_"]
    return "\n".join(lines)


def payroll_slip(employee: dict, period: str | None = None, save: bool = True) -> str:
    result = calculate_payroll(employee, period)
    if save:
        _save_slip(result)
    return _slip_text(employee, result)


def create_adjustment(nik: str, adjustment_type: str, amount, description: str = "",
                      recorded_by: str = "") -> str:
    employee = karyawan_service.get_karyawan(nik)
    if not employee:
        return f"❌ NIK *{karyawan_service.normalize_nik(nik)}* tidak ditemukan."
    value = parse_amount(amount)
    if value <= 0:
        return "❌ Jumlah tidak terbaca."
    kind = "Bonus" if adjustment_type.lower() == "bonus" else "Potongan"
    period = now_tz().strftime("%Y-%m")
    _ensure_sheet(ADJUSTMENT_SHEET, ADJUSTMENT_FIELDS)
    sheet_repo.append_row(ADJUSTMENT_SHEET, [
        now_tz().strftime("%Y-%m-%d"), employee["nik"], employee["nama"], period,
        kind, value, description.strip() or "-", karyawan_service.normalize_phone(recorded_by),
    ])
    return (f"{'🎁' if kind == 'Bonus' else '➖'} *{kind.upper()} TERCATAT*\n"
            f"👤 {employee['nama']} ({employee['nik']})\n📅 Periode gaji: {karyawan_service._period_label(period)}\n"
            f"💰 {format_rupiah(value)}\n🗒️ {description.strip() or '-'}")


def process_monthly_payroll(period: str | None = None, save: bool = False) -> str:
    period = _period(period)
    active = [employee for employee in karyawan_service._employees() if employee["aktif"]]
    if not active:
        return "⚠️ Belum ada karyawan aktif."
    attendance = karyawan_service._attendance_records()
    adjustments = sheet_repo.get_all_records(ADJUSTMENT_SHEET)
    total = 0
    lines = ["✅ *GAJI DIPROSES & DISIMPAN*" if save else "💼 *REKAP GAJI KARYAWAN*",
             f"📅 {karyawan_service._period_label(period)}", "━━━━━━━━━━━━━━━━━━"]
    for index, employee in enumerate(active, start=1):
        result = calculate_payroll(employee, period, attendance, adjustments)
        total += result["Gaji Bersih"]
        if save:
            _save_slip(result)
        suffix = (f" | ❌ A:{result['Alpha']}" if result["Alpha"] else "")
        suffix += f" | ⏰ T:{result['Terlambat']}" if result["Terlambat"] else ""
        lines += [f"{index}. *{employee['nama']}* ({employee['nik']})",
                  f"   💵 {format_rupiah(result['Gaji Bersih'])}{suffix}"]
    lines += ["━━━━━━━━━━━━━━━━━━", f"💰 *TOTAL GAJI BERSIH:* {format_rupiah(total)}",
              f"👥 {len(active)} karyawan"]
    if not save:
        lines.append(f"\n💡 `proses gaji {period}` → simpan semua slip ke sheet *{PAYROLL_SHEET}*")
    return "\n".join(lines)


def handle_payroll_command(message: str, sender: str) -> str | None:
    text = message.strip()
    match = re.fullmatch(r"(rekap|proses)\s+gaji(?:\s+(\d{4}-\d{2}))?", text, re.I)
    if match:
        if not karyawan_service.is_admin(sender):
            return karyawan_service.MSG_ADMIN_ONLY
        try:
            return process_monthly_payroll(match.group(2), save=match.group(1).lower() == "proses")
        except ValueError as error:
            return str(error)

    match = re.fullmatch(r"(?:slip\s+)?gaji\s+(\d{1,6})(?:\s+(\d{4}-\d{2}))?", text, re.I)
    if match:
        employee, error = karyawan_service._resolve_target(
            match.group(1), karyawan_service.is_admin(sender), karyawan_service.get_karyawan_by_phone(sender)
        )
        if error:
            return error
        if match.group(2) and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", match.group(2)):
            return "❌ Format periode salah. Gunakan yyyy-mm."
        return payroll_slip(employee, match.group(2))

    match = re.fullmatch(r"(bonus|potong|potongan|kasbon)\s+(\d{1,6})\s+([\d.,]+(?:\s*(?:rb|ribu|jt|juta|k)\b)?)(?:\s+(.+))?", text, re.I)
    if match:
        if not karyawan_service.is_admin(sender):
            return karyawan_service.MSG_ADMIN_ONLY
        kind = "Bonus" if match.group(1).lower() == "bonus" else "Potongan"
        return create_adjustment(match.group(2), kind, match.group(3), match.group(4) or "", sender)
    return None