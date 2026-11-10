from pydantic import BaseModel
from typing import List, Optional

class SalesItem(BaseModel):
    produk: str
    kategori: str
    keterangan: str
    kuantitas: float
    satuan: str
    penjualan: float
    tanggal: str

class GroqResponse(BaseModel):
    transaksi: List[dict]

class WebhookPayload(BaseModel):
    sender: str
    message: str
    secret: Optional[str] = None