from fastapi import APIRouter, Request, HTTPException
from app.config import config
from app.core.whatsapp import send_whatsapp
from app.services import sales_service
from app.services.setup_service import init_all_sheets # Import fungsi di atas

router = APIRouter()

@router.get("/setup")
def setup_database():
    init_all_sheets()
    return {"status": "success", "message": "Setup sheets selesai. Cek terminal untuk detail."}


@router.post("/webhook")
async def fonnte_webhook(request: Request):
    data = await request.json()
    
    if data.get("secret") != config.WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    sender = data.get("sender", "").replace("@c.us", "")
    message = data.get("message", "").strip()
    
    if not sender or len(message) < 3: return {"status": "ignored"}
    
    reply_r = sales_service.process_rekap(message)
    if reply_r:
        send_whatsapp(sender, reply_r)
        return {"status": "rekap"}
        
    is_sales, reply_s = sales_service.process_sales(message)
    if is_sales:
        send_whatsapp(sender, reply_s)
        return {"status": "saved"}
        
    return {"status": "ignored"}