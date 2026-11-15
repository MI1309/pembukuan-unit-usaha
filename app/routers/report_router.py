import re

from fastapi import APIRouter, HTTPException, Query

from app.services import report_service

router = APIRouter()


def _run_report(month: str, weekly: bool) -> dict:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="Parameter bulan harus berformat YYYY-MM.")
    try:
        message = (
            report_service.jalankan_rekap_mingguan(month)
            if weekly else report_service.jalankan_rekap_bulanan(month)
        )
        return {"status": "success", "message": message}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/report/mingguan")
def report_mingguan(bulan: str = Query(..., description="Bulan laporan dengan format YYYY-MM")):
    return _run_report(bulan, weekly=True)


@router.get("/report/bulanan")
def report_bulanan(bulan: str = Query(..., description="Bulan laporan dengan format YYYY-MM")):
    return _run_report(bulan, weekly=False)