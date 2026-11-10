from fastapi import FastAPI
from app.routers import webhook

app = FastAPI(title="Agrinova Bot API")

app.include_router(webhook.router)

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Bot Agrinova Aktif"}