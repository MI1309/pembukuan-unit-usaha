import calendar
import re
from datetime import date, datetime
from threading import RLock

from app.config import config
from app.core.utils import format_rupiah, now_tz, parse_amount
from app.repositories import sheet_repo

EMPLOYEE_SHEET = "Database Karyawan"
ATTENDANCE_SHEET = "Presensi Karyawan"
EMPLOYEE_FIELDS = [
    "NIK", "Nama Karyawan", "Jabatan", "Departemen", "Tanggal Masuk",
    "Gaji Pokok", "No. HP", "Alamat", "Status", "Tempat Lahir",
    "Tanggal Lahir", "Jenis Kelamin", "Status Nikah", "Pendidikan",
    "Pengalaman Kerja",
]
ATTENDANCE_FIELDS = [
    "Tanggal", "NIK", "Nama Karyawan", "Jam Masuk", "Jam Keluar",
    "Status", "Keterangan", "Lembur (jam)",
]
ADJUSTMENT_FIELDS = [
    "Tanggal", "NIK", "Nama", "Periode", "Jenis", "Jumlah (Rp)",
    "Keterangan", "Dicatat Oleh",
]
EDITABLE_FIELDS = {
    "nama": "Nama Karyawan", "jabatan": "Jabatan", "departemen": "Departemen",
    "dept": "Departemen", "gaji": "Gaji Pokok", "hp": "No. HP", "alamat": "Alamat",
}
LABEL_FIELDS = {
    "nama": "nama", "ttl": "ttl", "tempattgllahir": "ttl",
    "tempattanggallahir": "ttl", "tempatlahir": "ttl", "alamat": "alamat",
    "jeniskelamin": "jk", "jk": "jk", "kelamin": "jk", "status": "nikah",
    "statusnikah": "nikah", "statusperkawinan": "nikah", "pendidikan": "pendidikan",
    "pengalamankerja": "pengalaman", "pengalaman": "pengalaman",
    "posisitugas": "posisi", "posisi": "posisi", "jabatan": "posisi",
    "departemen": "dept", "dept": "dept", "gaji": "gaji", "gajipokok": "gaji",
    "nohp": "hp", "hp": "hp", "nomorhp": "hp", "telepon": "hp", "wa": "hp",
}
_WRITE_LOCK = RLock()
MSG_ADMIN_ONLY = "⛔ Perintah ini hanya untuk admin."


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    return "62" + digits[1:] if digits.startswith("0") else digits


def normalize_nik(nik) -> str:
    value = str(nik or "").strip()
    return value.zfill(3) if value.isdigit() else value


def is_admin(sender: str) -> bool:
    configured = config.ADMIN_SENDERS or config.ALLOWED_SENDERS
    sender_phone = normalize_phone(sender)
    if not configured:
        return True
    return bool(sender_phone) and sender_phone in {normalize_phone(value) for value in configured}


def _is_active(employee: dict) -> bool:
    return not re.search(r"non", str(employee.get("Status", "Aktif")).strip(), re.I)


def _employee_view(record: dict, row: int) -> dict:
    employee = dict(record)
    employee.update({
        "NIK": normalize_nik(record.get("NIK")),
        "Status": str(record.get("Status") or "Aktif").strip(),
        "row": row,
        "nik": normalize_nik(record.get("NIK")),
        "nama": str(record.get("Nama Karyawan") or ""),
        "jabatan": str(record.get("Jabatan") or "-"),
        "departemen": str(record.get("Departemen") or "-"),
        "tglMasuk": _date_string(record.get("Tanggal Masuk")),
        "gaji": parse_amount(record.get("Gaji Pokok", 0)),
        "hp": str(record.get("No. HP") or ""),
        "alamat": str(record.get("Alamat") or "-"),
        "aktif": _is_active(record),
        "tempatLahir": str(record.get("Tempat Lahir") or "-"),
        "tglLahir": _date_string(record.get("Tanggal Lahir")),
        "jk": str(record.get("Jenis Kelamin") or "-"),
        "statusNikah": str(record.get("Status Nikah") or "-"),
        "pendidikan": str(record.get("Pendidikan") or "-"),
        "pengalaman": str(record.get("Pengalaman Kerja") or "-"),
    })
    return employee


def _employees() -> list[dict]:
    return [
        _employee_view(record, row_number)
        for row_number, record in enumerate(sheet_repo.get_all_records(EMPLOYEE_SHEET), start=2)
        if str(record.get("NIK", "")).strip()
    ]


def get_karyawan(nik: str) -> dict | None:
    target = normalize_nik(nik)
    return next((employee for employee in _employees() if employee["nik"] == target), None)


def get_karyawan_by_phone(phone: str, active_only: bool = False) -> dict | None:
    target = normalize_phone(phone)
    if not target:
        return None
    for employee in _employees():
        if normalize_phone(employee["hp"]) == target and (employee["aktif"] or not active_only):
            return employee
    return None


def _next_nik() -> str:
    highest = max((int(employee["nik"]) for employee in _employees() if employee["nik"].isdigit()), default=0)
    return f"{highest + 1:03d}"


def _date_string(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text[:10]


def _parse_ymd(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _parse_ttl(value: str) -> tuple[str, str]:
    match = re.match(r"^(.*?)[,\s]*(\d{1,2})[\s\-/\.]*([a-zA-Z]+|\d{1,2})[\s\-/\.]*(\d{4})$", value.strip())
    if not match:
        return value.strip(), ""
    place, day, month_value, year = match.groups()
    months = [month.lower()[:3] for month in config.BULAN]
    month_value = month_value.lower()
    if month_value.isdigit():
        month = int(month_value)
    else:
        month_key = "agu" if month_value.startswith("agt") else month_value[:3]
        month = next((index + 1 for index, name in enumerate(months) if name == month_key), 0)
    try:
        birthday = date(int(year), month, int(day))
    except ValueError:
        return place.strip() or value.strip(), ""
    return place.strip(), birthday.isoformat()


def _format_date(value: str) -> str:
    parsed = _parse_ymd(value)
    return f"{parsed.day} {config.BULAN[parsed.month - 1]} {parsed.year}" if parsed else "-"


def _tenure(hire_date: str) -> str:
    hired = _parse_ymd(hire_date)
    if not hired or hired > now_tz().date():
        return "-"
    current = now_tz().date()
    months = (current.year - hired.year) * 12 + current.month - hired.month
    if current.day < hired.day:
        months -= 1
    return (f"{months // 12} th " if months // 12 else "") + f"{months % 12} bln"


def _duplicate_phone(phone: str, exclude_nik: str = "") -> dict | None:
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    return next((employee for employee in _employees()
                 if employee["nik"] != normalize_nik(exclude_nik)
                 and normalize_phone(employee["hp"]) == normalized), None)


def _add_employee(values: dict) -> str:
    name = str(values.get("nama") or "").strip()
    if not name:
        return "❌ Baris *Nama:* wajib diisi."
    salary = parse_amount(values.get("gaji", 0))
    phone = normalize_phone(values.get("hp", ""))
    place, birthday = _parse_ttl(str(values.get("ttl") or ""))
    position = str(values.get("posisi") or "-").strip()
    department = str(values.get("dept") or "").strip()
    if not department and "," in position:
        position, department = [part.strip() for part in position.split(",", 1)]
    department = department or "-"
    gender = str(values.get("jk") or "-").strip()
    if gender.lower().startswith("p"):
        gender = "Perempuan"
    elif gender.lower().startswith("l"):
        gender = "Laki-laki"
    with _WRITE_LOCK:
        duplicate = _duplicate_phone(phone)
        if duplicate:
            return f"⚠️ No. HP sudah dipakai *{duplicate['nama']}* ({duplicate['nik']})."
        nik = _next_nik()
        row = [
            nik, name, position or "-", department, now_tz().strftime("%Y-%m-%d"), salary,
            phone, str(values.get("alamat") or ""), "Aktif", place or "-", birthday,
            gender, str(values.get("nikah") or "-"), str(values.get("pendidikan") or "-"),
            str(values.get("pengalaman") or "-"),
        ]
        sheet_repo.append_row(EMPLOYEE_SHEET, row)
    return (
        "✅ *KARYAWAN DITAMBAHKAN*\n━━━━━━━━━━━━━━━━━━\n"
        f"🆔 NIK: *{nik}*\n👤 Nama: *{name}*\n"
        f"🎂 TTL: {place or '-'}{', ' + _format_date(birthday) if birthday else ''}\n"
        f"⚧ Jenis kelamin: {gender}\n💍 Status: {values.get('nikah') or '-'}\n"
        f"🎓 Pendidikan: {values.get('pendidikan') or '-'}\n🛠️ Pengalaman: {values.get('pengalaman') or '-'}\n"
        f"💼 Jabatan: {position or '-'}\n🏢 Departemen: {department}\n"
        f"🏠 Alamat: {values.get('alamat') or '-'}\n📱 No. HP: {phone or '-'}\n"
        f"💰 Gaji Pokok: {format_rupiah(salary)}\n━━━━━━━━━━━━━━━━━━"
    )


def parse_form_karyawan(raw: str) -> dict:
    values = {}
    text = re.sub(r"^\s*tambah\s+karyawan\s*", "", str(raw or ""), flags=re.I)
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = re.sub(r"[^a-z]", "", key.lower())
        normalized_value = value.strip()
        if normalized_value and normalized_key in LABEL_FIELDS:
            values[LABEL_FIELDS[normalized_key]] = normalized_value
    return values


def add_karyawan(raw: str) -> str:
    if not raw.strip():
        return (
            "📋 *TEMPLATE TAMBAH KARYAWAN*\n"
            "```\nTambah karyawan\nNama: \nTempat tgl lahir: \nAlamat: \nJenis kelamin: \n"
            "Status: \nPendidikan: \nPengalaman kerja: \nPosisi tugas: \nDepartemen: \nGaji: \nNo HP: \n```\n"
            "Format cepat: `tambah karyawan Nama | Jabatan | Dept | Gaji | No HP | Alamat`"
        )
    if "\n" in raw and ":" in raw:
        values = parse_form_karyawan(raw)
        if not values.get("nama"):
            return "❌ Baris *Nama:* wajib diisi. Gunakan `form karyawan` untuk template."
        return _add_employee(values)
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) < 4 or not parts[0]:
        return "Format: `tambah karyawan Nama | Jabatan | Departemen | Gaji | No HP | Alamat`."
    salary = parse_amount(parts[3])
    if salary <= 0:
        return "❌ Gaji pokok tidak terbaca."
    return _add_employee({
        "nama": parts[0], "posisi": parts[1] or "-", "dept": parts[2] or "-",
        "gaji": salary, "hp": parts[4] if len(parts) > 4 else "",
        "alamat": parts[5] if len(parts) > 5 else "",
    })


def update_karyawan(nik: str, field: str, value: str) -> str:
    employee = get_karyawan(nik)
    column_name = EDITABLE_FIELDS.get(field.strip().lower())
    if not employee:
        return f"❌ NIK *{normalize_nik(nik)}* tidak ditemukan."
    if not column_name:
        return "Field: nama, jabatan, departemen, gaji, hp, alamat."
    normalized_value = value.strip()
    if column_name == "Gaji Pokok":
        normalized_value = parse_amount(normalized_value)
        if normalized_value <= 0:
            return "❌ Nominal gaji tidak terbaca."
    elif column_name == "No. HP":
        normalized_value = normalize_phone(normalized_value)
    column = EMPLOYEE_FIELDS.index(column_name) + 1
    with _WRITE_LOCK:
        if column_name == "No. HP":
            duplicate = _duplicate_phone(normalized_value, employee["nik"])
            if duplicate:
                return f"⚠️ No. HP sudah dipakai *{duplicate['nama']}* ({duplicate['nik']})."
        sheet_repo.update_cell(EMPLOYEE_SHEET, employee["row"], column, normalized_value)
    shown = format_rupiah(normalized_value) if column_name == "Gaji Pokok" else normalized_value
    return f"✅ *{employee['nama']}* ({employee['nik']}) diperbarui\n{column_name} → *{shown}*"


def set_status_karyawan(nik: str, active: bool) -> str:
    employee = get_karyawan(nik)
    if not employee:
        return f"❌ NIK *{normalize_nik(nik)}* tidak ditemukan."
    with _WRITE_LOCK:
        sheet_repo.update_cell(EMPLOYEE_SHEET, employee["row"], 9, "Aktif" if active else "Nonaktif")
    return ("✅ " if active else "🚫 ") + f"*{employee['nama']}* ({employee['nik']}) sekarang *{'AKTIF' if active else 'NONAKTIF'}*."


def delete_karyawan(nik: str) -> str:
    with _WRITE_LOCK:
        employee = get_karyawan(nik)
        if not employee:
            return f"❌ NIK *{normalize_nik(nik)}* tidak ditemukan."
        sheet_repo.delete_row(EMPLOYEE_SHEET, employee["row"])
    return f"✅ Data karyawan *{employee['nama']}* ({employee['nik']}) telah dihapus dari database. Riwayat presensi tetap tersimpan."


def _attendance_records() -> list[dict]:
    result = []
    for row_number, record in enumerate(sheet_repo.get_all_records(ATTENDANCE_SHEET), start=2):
        row = dict(record)
        row.update({"row": row_number, "ymd": _date_string(record.get("Tanggal")),
                    "nik": normalize_nik(record.get("NIK")),
                    "status": str(record.get("Status") or "").strip().capitalize(),
                    "masuk": str(record.get("Jam Masuk") or "-"),
                    "keluar": str(record.get("Jam Keluar") or "-"),
                    "ket": str(record.get("Keterangan") or ""),
                    "lembur": parse_amount(record.get("Lembur (jam)", 0))})
        result.append(row)
    return result


def _attendance_for(employee: dict, ymd: str) -> dict | None:
    return next((row for row in _attendance_records()
                 if row["ymd"] == ymd and row["nik"] == employee["nik"]), None)


def _append_attendance(employee: dict, ymd: str, masuk: str, keluar: str,
                       status: str, reason: str, overtime: int = 0) -> None:
    sheet_repo.append_row(ATTENDANCE_SHEET, [
        ymd, employee["nik"], employee["nama"], masuk, keluar, status, reason, overtime,
    ])


def _update_attendance(record: dict, changes: dict[str, object]) -> None:
    columns = {"Jam Masuk": 4, "Jam Keluar": 5, "Status": 6, "Keterangan": 7, "Lembur (jam)": 8}
    with _WRITE_LOCK:
        for field, value in changes.items():
            sheet_repo.update_cell(ATTENDANCE_SHEET, record["row"], columns[field], value)


def _minutes(value: str) -> int:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or ""))
    return int(match.group(1)) * 60 + int(match.group(2)) if match else 0


def _record_attendance(employee: dict, action: str, reason: str = "", ymd: str | None = None,
                       by_admin: bool = False) -> str:
    now = now_tz()
    today = ymd or now.strftime("%Y-%m-%d")
    record = _attendance_for(employee, today)
    current_time = now.strftime("%H:%M") if today == now.strftime("%Y-%m-%d") else "-"

    if action == "masuk":
        if record:
            if record["status"] in {"Hadir", "Terlambat"}:
                return f"⚠️ *{employee['nama']}* sudah presensi masuk hari ini pukul {record['masuk']}."
            return f"⚠️ *{employee['nama']}* sudah tercatat *{record['status']}* hari ini."
        late_minutes = max(0, _minutes(current_time) - _minutes(config.JAM_MASUK))
        late = late_minutes > int(getattr(config, "TOLERANSI_TELAT_MENIT", 0))
        late_note = f"Terlambat ({late_minutes} menit)" if late else "-"
        _append_attendance(employee, today, current_time, "-", "Hadir", late_note)
        return (f"✅ *PRESENSI MASUK TERCATAT*\n👤 {employee['nama']} ({employee['nik']})\n"
                f"🕐 Masuk: *{current_time}*\n" + (f"⏰ *Terlambat {late_minutes} menit*" if late else "👍 Tepat waktu"))

    if action == "pulang":
        if not record:
            return f"⚠️ *{employee['nama']}* belum presensi masuk hari ini. Ketik `masuk` dulu."
        if record["status"] not in {"Hadir", "Terlambat"}:
            return f"⚠️ Hari ini tercatat *{record['status']}*, tidak bisa presensi pulang."
        if record["keluar"] not in {"", "-"}:
            return f"⚠️ *{employee['nama']}* sudah pulang pukul {record['keluar']}."
        overtime = max(0, (_minutes(current_time) - _minutes(config.JAM_KELUAR)) // 60)
        changes: dict[str, object] = {"Jam Keluar": current_time, "Lembur (jam)": overtime}
        early = _minutes(current_time) < _minutes(config.JAM_KELUAR)
        if early:
            changes["Keterangan"] = (record["ket"] + " | " if record["ket"] not in {"", "-"} else "") + "Pulang awal"
        _update_attendance(record, changes)
        return (f"✅ *PRESENSI PULANG TERCATAT*\n👤 {employee['nama']} ({employee['nik']})\n"
                f"🌅 Masuk: {record['masuk']}\n🌇 Keluar: *{current_time}*\n" +
                ("⚠️ Pulang lebih awal\n" if early else "") +
                (f"⏱️ Lembur: *{overtime} jam*" if overtime else ""))

    status = action.capitalize()
    if record:
        if record["status"] == status and not reason:
            return f"⚠️ *{employee['nama']}* sudah tercatat *{status}* pada {_format_date(today)}."
        if not by_admin and record["status"] == "Hadir":
            return "⚠️ Anda sudah presensi *Hadir* hari ini. Hubungi admin untuk koreksi."
        note = reason.strip() or ("Input admin" if by_admin else "-")
        _update_attendance(record, {
            "Status": status, "Keterangan": note,
            "Jam Masuk": "-", "Jam Keluar": "-", "Lembur (jam)": 0,
        })
        return f"🔄 *PRESENSI DIKOREKSI*\n👤 {employee['nama']} ({employee['nik']})\n{record['status']} → *{status}*"
    note = reason.strip() or ("Input admin" if by_admin else "-")
    _append_attendance(employee, today, "-", "-", status, note)
    return f"✅ *PRESENSI TERCATAT*\n👤 {employee['nama']} ({employee['nik']})\n📅 {_format_date(today)}\n📝 Status: *{status}*\n🗒️ {note}"


def record_attendance(employee: dict, action: str, reason: str = "", ymd: str | None = None,
                      by_admin: bool = False) -> str:
    with _WRITE_LOCK:
        return _record_attendance(employee, action, reason, ymd, by_admin)


def _month_days(period: str, hire_date: str = "") -> list[str]:
    year, month = map(int, period.split("-"))
    today = now_tz().date()
    hire = _parse_ymd(hire_date)
    holidays = set(getattr(config, "HARI_LIBUR", []))
    days_off = set(getattr(config, "HARI_LIBUR_MINGGUAN", [6]))
    output = []
    for day_number in range(1, calendar.monthrange(year, month)[1] + 1):
        candidate = date(year, month, day_number)
        if candidate >= today:
            break
        if hire and candidate < hire:
            continue
        if candidate.weekday() in days_off or candidate.isoformat() in holidays:
            continue
        output.append(candidate.isoformat())
    return output


def attendance_stats(employee: dict, period: str | None = None, records: list[dict] | None = None) -> dict:
    period = period or now_tz().strftime("%Y-%m")
    stats = {"hadir": 0, "terlambat": 0, "izin": 0, "sakit": 0, "cuti": 0,
             "alpha": 0, "alphaOtomatis": 0, "lemburJam": 0}
    rows = records if records is not None else _attendance_records()
    matching = [row for row in rows if normalize_nik(row.get("NIK", row.get("nik"))) == employee["nik"]
                and str(row.get("Tanggal", row.get("ymd", "")))[:7] == period]
    recorded_dates = set()
    for row in matching:
        day = _date_string(row.get("Tanggal", row.get("ymd")))
        recorded_dates.add(day)
        status = str(row.get("Status", row.get("status", ""))).strip().lower()
        note = str(row.get("Keterangan", row.get("ket", "")))
        if status in {"hadir", "terlambat"}:
            stats["hadir"] += 1
            if status == "terlambat" or re.search(r"terlambat", note, re.I):
                stats["terlambat"] += 1
            stats["lemburJam"] += parse_amount(row.get("Lembur (jam)", row.get("lembur", 0)))
        elif status in {"izin", "sakit", "cuti", "alpha"}:
            stats[status] += 1

    if getattr(config, "AUTO_ALPHA", True) and employee["aktif"]:
        for workday in _month_days(period, employee["tglMasuk"]):
            if workday not in recorded_dates:
                stats["alpha"] += 1
                stats["alphaOtomatis"] += 1
    return stats


def _period_label(period: str) -> str:
    year, month = map(int, period.split("-"))
    return f"{config.BULAN[month - 1]} {year}"


def _valid_period(period: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period or ""))


def _profile_text(employee: dict) -> str:
    hire_date = employee["tglMasuk"]
    return (
        "👤 *DATA KARYAWAN*\n━━━━━━━━━━━━━━━━━━\n"
        f"🆔 NIK: *{employee['nik']}*\n👤 Nama: *{employee['nama']}*\n"
        f"💼 Jabatan: {employee['jabatan']}\n🏢 Departemen: {employee['departemen']}\n"
        f"📅 Masuk: {_format_date(hire_date)} ({_tenure(hire_date)})\n"
        f"💰 Gaji Pokok: {format_rupiah(employee['gaji'])}\n📱 No. HP: {employee['hp'] or '-'}\n"
        f"🏠 Alamat: {employee['alamat']}\n🎂 TTL: {employee['tempatLahir']}"
        f"{', ' + _format_date(employee['tglLahir']) if _parse_ymd(employee['tglLahir']) else ''}\n"
        f"⚧ Jenis kelamin: {employee['jk']}\n💍 Status nikah: {employee['statusNikah']}\n"
        f"🎓 Pendidikan: {employee['pendidikan']}\n🛠️ Pengalaman: {employee['pengalaman']}\n"
        f"📌 Status: *{employee['Status']}*\n━━━━━━━━━━━━━━━━━━"
    )


def _list_employees(mode: str = "aktif") -> str:
    employees = _employees()
    if mode == "nonaktif":
        selected = [employee for employee in employees if not employee["aktif"]]
    elif mode == "semua":
        selected = employees
    else:
        selected = [employee for employee in employees if employee["aktif"]]
    if not selected:
        return "⚠️ Tidak ada data karyawan untuk pilihan tersebut."
    lines = [f"👥 *DAFTAR KARYAWAN{'' if mode == 'aktif' else ' (' + mode.upper() + ')'}*",
             f"_{config.COMPANY}_", "━━━━━━━━━━━━━━━━━━"]
    for index, employee in enumerate(selected, start=1):
        suffix = " 🚫" if not employee["aktif"] else ""
        lines += [f"{index}. *{employee['nama']}*{suffix}",
                  f"   🆔 {employee['nik']} | 💼 {employee['jabatan']} | 🏢 {employee['departemen']}"]
    lines += ["━━━━━━━━━━━━━━━━━━", f"📊 Total: *{len(selected)} karyawan*"]
    return "\n".join(lines)


def _today_attendance() -> str:
    employees = [employee for employee in _employees() if employee["aktif"]]
    today = now_tz().strftime("%Y-%m-%d")
    records = {row["nik"]: row for row in _attendance_records() if row["ymd"] == today}
    if not employees:
        return "⚠️ Belum ada karyawan aktif."
    counts = {"hadir": 0, "telat": 0, "lain": 0, "belum": 0}
    lines = ["📋 *PRESENSI HARI INI*", f"📅 {_format_date(today)}", "━━━━━━━━━━━━━━━━━━"]
    for index, employee in enumerate(employees, start=1):
        record = records.get(employee["nik"])
        if not record:
            icon, detail = "⏳", "belum presensi"
            counts["belum"] += 1
        elif record["status"] in {"Hadir", "Terlambat"}:
            late = record["status"] == "Terlambat" or bool(re.search("terlambat", record["ket"], re.I))
            icon = "⏰" if late else "✅"
            counts["hadir"] += 1
            counts["telat"] += int(late)
            detail = "masuk " + record["masuk"] + (" / pulang " + record["keluar"] if record["keluar"] != "-" else "")
        else:
            icon, detail = "❌" if record["status"] == "Alpha" else "🏥", record["status"]
            counts["lain"] += 1
        lines.append(f"{index}. {icon} *{employee['nama']}* — {detail}")
    lines += ["━━━━━━━━━━━━━━━━━━", f"✅ Hadir: {counts['hadir']} (⏰ telat {counts['telat']}) | 🏥/❌: {counts['lain']} | ⏳ Belum: {counts['belum']}"]
    return "\n".join(lines)


def _attendance_summary(nik: str | None, period: str) -> str:
    targets = [employee for employee in _employees()
               if employee["aktif"] and (not nik or employee["nik"] == normalize_nik(nik))]
    if not targets:
        return f"📊 *REKAP PRESENSI {_period_label(period)}*\n_Karyawan tidak ditemukan / tidak aktif._"
    rows = _attendance_records()
    lines = ["📊 *REKAP PRESENSI KARYAWAN*", f"📅 {_period_label(period)}", "━━━━━━━━━━━━━━━━━━"]
    for index, employee in enumerate(targets, start=1):
        stats = attendance_stats(employee, period, rows)
        lines.append(f"{index}. *{employee['nama']}* ({employee['nik']})")
        lines.append(f"   ✅ H:{stats['hadir']} | ⏰ T:{stats['terlambat']} | 🏥 I/S:{stats['izin'] + stats['sakit']} | 🌴 C:{stats['cuti']} | ❌ A:{stats['alpha']}" +
                     (f" ({stats['alphaOtomatis']} otomatis)" if stats["alphaOtomatis"] else "") +
                     (f" | ⏱️ L:{stats['lemburJam']}j" if stats["lemburJam"] else ""))
    lines += ["━━━━━━━━━━━━━━━━━━", f"📊 Total: *{len(targets)} karyawan*"]
    return "\n".join(lines)


def _help_text(admin: bool) -> str:
    lines = [
        "👥 *PANDUAN LENGKAP KARYAWAN*", f"_{config.COMPANY}_", "━━━━━━━━━━━━━━━━━━",
        "🕐 *FITUR KARYAWAN (Via No. HP Terdaftar)*",
        "• `masuk` → Catat jam masuk",
        "• `pulang` → Catat jam pulang dan lembur",
        "• `sakit demam` / `izin acara keluarga` / `cuti keperluan keluarga`",
        "• `presensi saya` → Rekap presensi bulan ini",
        "• `slip gaji` / `slip gaji 2026-10` → Slip gaji sendiri",
        "• `cek data saya` → Lihat profil pribadi",
        "", "📋 *SALIN & KIRIM PESAN*",
        "`masuk`",
        "`pulang`",
        "`izin acara keluarga`",
        "`presensi saya`",
        "`slip gaji`",
    ]
    if admin:
        lines += [
            "", "🛠️ *MENU KELOLA DATA (KHUSUS ADMIN)*", "──────────────────",
            "➕ *1. TAMBAH KARYAWAN*",
            "• `form karyawan` → Minta template formulir",
            "• Format cepat: `tambah karyawan Nama | Jabatan | Dept | Gaji | No HP | Alamat`",
            "• Formulir lengkap: kirim beberapa baris `Nama:`, `Posisi tugas:`, `Departemen:`, `Gaji:`, `No HP:`, dan data lain",
            "", "🔍 *2. CEK DAN DAFTAR KARYAWAN*",
            "• `daftar karyawan` → Karyawan aktif",
            "• `daftar karyawan semua` / `daftar karyawan nonaktif`",
            "• `cek karyawan 001` → Biodata lengkap",
            "", "✏️ *3. UBAH DATA KARYAWAN*",
            "Format: `ubah karyawan <NIK> <field> <nilai baru>`",
            "• `ubah karyawan 001 gaji 5jt`",
            "• `ubah karyawan 001 hp 081234567890`",
            "• `ubah karyawan 001 jabatan Supervisor`",
            "• `ubah karyawan 001 departemen Produksi`",
            "• `ubah karyawan 001 alamat Jl. Sudirman No. 5`",
            "", "🚫 *4. STATUS KARYAWAN*",
            "• `nonaktifkan karyawan 001` / `aktifkan karyawan 001`",
            "• `hapus karyawan 001` lalu `hapus karyawan 001 konfirmasi` → Hapus permanen data profil",
            "", "💼 *5. PRESENSI DAN PENGGAJIAN*",
            "• `presensi hari ini` → Ringkasan kehadiran",
            "• `masuk 001` / `pulang 001` → Catat presensi karyawan",
            "• `presensi 001 izin alasan` / `presensi 001 alpha 2026-10-15 keterangan`",
            "• `rekap presensi` / `rekap presensi 001 2026-10`",
            "• `bonus 001 500rb THR` / `potong 001 100rb kasbon`",
            "• `slip gaji 001` → Tampilkan dan simpan slip",
            "• `rekap gaji` → Pratinjau total gaji bulan ini",
            "• `proses gaji 2026-10` → Proses dan simpan slip semua karyawan",
        ]
    lines.append("━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def _resolve_target(nik: str | None, admin: bool, self_employee: dict | None) -> tuple[dict | None, str | None]:
    if nik:
        employee = get_karyawan(nik)
        if not employee:
            return None, f"❌ NIK *{normalize_nik(nik)}* tidak ditemukan."
        if not admin and not (self_employee and self_employee["nik"] == employee["nik"]):
            return None, MSG_ADMIN_ONLY
    elif self_employee:
        employee = self_employee
    else:
        return None, "⚠️ Sebutkan NIK. Contoh: `masuk 001`."
    if not employee["aktif"]:
        return None, f"⚠️ Karyawan *{employee['nama']}* berstatus NONAKTIF."
    return employee, None


def handle_karyawan(message: str, sender: str) -> str | None:
    original = str(message or "").strip()
    admin = is_admin(sender)
    self_employee = get_karyawan_by_phone(sender)
    if re.match(r"^tambah\s+karyawan\b", original, re.I) and "\n" in original and ":" in original:
        return add_karyawan(original) if admin else MSG_ADMIN_ONLY

    text = re.sub(r"\s+", " ", original).strip()
    if re.match(r"^(help|menu|bantuan|panduan)\s+karyawan$", text, re.I) or (
        not admin and re.match(r"^(help|menu|bantuan|panduan|\?|halo|hai|hi)$", text, re.I)
    ):
        return _help_text(admin)
    if re.match(r"^(form|template)\s+karyawan$", text, re.I):
        return add_karyawan("") if admin else MSG_ADMIN_ONLY

    match = re.fullmatch(r"(masuk|datang|pulang|keluar)(?:\s+(\d{1,6}))?", text, re.I)
    if match:
        employee, error = _resolve_target(match.group(2), admin, self_employee)
        if error:
            return error
        action = "masuk" if match.group(1).lower() in {"masuk", "datang"} else "pulang"
        if action == "masuk" and _attendance_for(employee, now_tz().strftime("%Y-%m-%d")):
            record = _attendance_for(employee, now_tz().strftime("%Y-%m-%d"))
            if record["status"] in {"Hadir", "Terlambat"} and record["keluar"] == "-" and _minutes(now_tz().strftime("%H:%M")) >= _minutes(config.JAM_KELUAR):
                action = "pulang"
        return record_attendance(employee, action)

    match = re.fullmatch(r"(izin|sakit|cuti)(?:\s+(\d{1,6}))?(?:\s+(.+))?", text, re.I)
    if match and (self_employee or match.group(2)):
        employee, error = _resolve_target(match.group(2), admin, self_employee)
        if error:
            return error
        return record_attendance(employee, match.group(1).lower(), match.group(3) or "", by_admin=admin)

    if re.fullmatch(r"presensi\s+hari\s+ini", text, re.I):
        return _today_attendance() if admin else MSG_ADMIN_ONLY
    if re.fullmatch(r"(presensi|absen)\s+(saya|bulan\s+ini)", text, re.I) and self_employee:
        return _attendance_summary(self_employee["nik"], now_tz().strftime("%Y-%m"))

    match = re.fullmatch(r"(?:presensi|absen)\s+(\d{1,6})\s+(hadir|izin|sakit|cuti|alpha)(?:\s+(\d{4}-\d{2}-\d{2}))?(?:\s+(.+))?", text, re.I)
    if match:
        employee, error = _resolve_target(match.group(1), admin, self_employee)
        if error:
            return error
        status, requested_date, reason = match.group(2).lower(), match.group(3) or "", match.group(4) or ""
        if requested_date and not admin:
            return MSG_ADMIN_ONLY
        if requested_date and not _parse_ymd(requested_date):
            return "❌ Format tanggal salah. Gunakan yyyy-mm-dd."
        target_date = requested_date or now_tz().strftime("%Y-%m-%d")
        if status == "hadir" and target_date == now_tz().strftime("%Y-%m-%d"):
            return record_attendance(employee, "masuk", by_admin=admin)
        return record_attendance(employee, status, reason, target_date, admin)

    match = re.fullmatch(r"rekap\s+presensi(?:\s+(\d{1,6}))?(?:\s+(\d{4}-\d{2}))?", text, re.I)
    if match:
        nik, period = match.group(1), match.group(2) or now_tz().strftime("%Y-%m")
        if not _valid_period(period):
            return "❌ Format periode salah. Gunakan yyyy-mm."
        if not admin and (not self_employee or (nik and normalize_nik(nik) != self_employee["nik"])):
            return MSG_ADMIN_ONLY
        return _attendance_summary(normalize_nik(nik) if nik else None, period)

    if re.fullmatch(r"(?:rekap|proses)\s+gaji(?:\s+\d{4}-\d{2})?", text, re.I):
        if not admin:
            return MSG_ADMIN_ONLY
        return None

    match = re.fullmatch(r"(?:slip\s+)?gaji\s+(\d{1,6})(?:\s+(\d{4}-\d{2}))?", text, re.I)
    if match:
        employee, error = _resolve_target(match.group(1), admin, self_employee)
        if error:
            return error
        if match.group(2) and not _valid_period(match.group(2)):
            return "❌ Format periode salah. Gunakan yyyy-mm."
        from app.services.payroll_service import payroll_slip
        return payroll_slip(employee, match.group(2))
    if re.fullmatch(r"(?:slip(?:\s+gaji)?|gaji)(?:\s+saya)?(?:\s+\d{4}-\d{2})?", text, re.I) and self_employee:
        if not self_employee["aktif"]:
            return f"⚠️ Karyawan *{self_employee['nama']}* berstatus NONAKTIF."
        period_match = re.search(r"\d{4}-\d{2}", text)
        from app.services.payroll_service import payroll_slip
        return payroll_slip(self_employee, period_match.group(0) if period_match else None)

    match = re.fullmatch(r"(bonus|potong|potongan|kasbon)\s+(\d{1,6})\s+([\d.,]+(?:\s*(?:rb|ribu|jt|juta|k)\b)?)(?:\s+(.+))?", text, re.I)
    if match:
        if not admin:
            return MSG_ADMIN_ONLY
        employee = get_karyawan(match.group(2))
        if not employee:
            return f"❌ NIK *{normalize_nik(match.group(2))}* tidak ditemukan."
        amount = parse_amount(match.group(3))
        if amount <= 0:
            return "❌ Jumlah tidak terbaca."
        from app.services.payroll_service import create_adjustment
        kind = "Bonus" if match.group(1).lower() == "bonus" else "Potongan"
        return create_adjustment(employee["nik"], kind, amount, match.group(4) or "", sender)

    match = re.fullmatch(r"(?:daftar|list)\s+karyawan(?:\s+(semua|nonaktif))?", text, re.I)
    if match:
        return _list_employees((match.group(1) or "aktif").lower()) if admin else MSG_ADMIN_ONLY

    match = re.fullmatch(r"(?:cek\s+)?(?:karyawan|data|nik)\s+(\d{1,6})", text, re.I)
    if match:
        employee = get_karyawan(match.group(1))
        if not employee:
            return f"❌ NIK *{normalize_nik(match.group(1))}* tidak ditemukan di database."
        if not admin and not (self_employee and self_employee["nik"] == employee["nik"]):
            return MSG_ADMIN_ONLY
        return _profile_text(employee)
    if re.fullmatch(r"(?:cek\s+)?(?:profil|data\s+saya|karyawan\s+saya)", text, re.I) and self_employee:
        return _profile_text(self_employee)

    match = re.fullmatch(r"tambah\s+karyawan(?:\s+(.+))?", text, re.I)
    if match:
        return add_karyawan(match.group(1) or "") if admin else MSG_ADMIN_ONLY

    match = re.fullmatch(r"hapus\s+karyawan\s+(\d{1,6})(?:\s+(konfirmasi))?", text, re.I)
    if match:
        if not admin:
            return MSG_ADMIN_ONLY
        nik = normalize_nik(match.group(1))
        if match.group(2):
            return delete_karyawan(nik)
        employee = get_karyawan(nik)
        if not employee:
            return f"❌ NIK *{nik}* tidak ditemukan."
        return (f"⚠️ Anda akan menghapus profil *{employee['nama']}* ({employee['nik']}) secara permanen.\n"
                f"Untuk melanjutkan, kirim `hapus karyawan {employee['nik']} konfirmasi`. Riwayat presensi tetap tersimpan.")
    if re.fullmatch(r"hapus\s+karyawan", text, re.I):
        return "Format: `hapus karyawan <NIK>`, lalu konfirmasi dengan `hapus karyawan <NIK> konfirmasi`." if admin else MSG_ADMIN_ONLY

    match = re.fullmatch(r"ubah\s+karyawan\s+(\d{1,6})\s+(nama|jabatan|departemen|dept|gaji|hp|alamat)\s+(.+)", text, re.I)
    if match:
        return update_karyawan(match.group(1), match.group(2), match.group(3)) if admin else MSG_ADMIN_ONLY
    if re.match(r"^ubah\s+karyawan\b", text, re.I):
        return ("💡 Format: `ubah karyawan 001 gaji 6jt`\nField: nama, jabatan, departemen, gaji, hp, alamat"
                if admin else MSG_ADMIN_ONLY)

    match = re.fullmatch(r"(aktifkan|nonaktifkan)\s+karyawan\s+(\d{1,6})", text, re.I)
    if match:
        return set_status_karyawan(match.group(2), match.group(1).lower() == "aktifkan") if admin else MSG_ADMIN_ONLY
    return None