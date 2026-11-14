from fastapi import FastAPI
from app.routers import report_router, webhook

app = FastAPI(title="Agrinova Bot API")

app.include_router(webhook.router)
app.include_router(report_router.router)

@app.get("/")
async def health_check():
    return {"status": "ok", "message": "Bot Agrinova Aktif"}