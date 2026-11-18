import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Header
from app.config import config
from app.services import archive_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cron", tags=["Cron Jobs"])

DEFAULT_CRON_KEY = "agrinova2026"


def _verify_cron_key(key: Optional[str], x_cron_key: Optional[str] = None):
    expected_key = getattr(config, "CRON_SECRET", None) or getattr(config, "WEBHOOK_SECRET", None) or DEFAULT_CRON_KEY
    provided = key or x_cron_key
    if not provided or provided != expected_key:
        logger.warning("Akses cron ditolak: invalid key %s", provided)
        raise HTTPException(status_code=403, detail="Akses ditolak: Kunci cron tidak valid")


@router.get("/archive")
@router.post("/archive")
async def trigger_monthly_archive(
    key: Optional[str] = Query(None, description="Kunci rahasia cron"),
    x_cron_key: Optional[str] = Header(None, description="Kunci rahasia via header"),
    year: Optional[int] = Query(None, description="Tahun yang ingin diarsipkan (opsional)"),
    month: Optional[int] = Query(None, description="Bulan yang ingin diarsipkan 1-12 (opsional)"),
):
    """Endpoint yang dipanggil oleh cron-job.org setiap awal bulan (misal tanggal 1 jam 00:05).
    Jika year dan month tidak diberikan, otomatis mengarsipkan data bulan sebelumnya."""
    _verify_cron_key(key, x_cron_key)

    target_year, target_month = archive_service.get_target_archive_month(year, month)
    try:
        result = archive_service.archive_month_ledger(target_year, target_month)
        return result
    except Exception as exc:
        logger.error("Gagal menjalankan arsip bulanan via cron: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Gagal melakukan pengarsipan: {exc}")
