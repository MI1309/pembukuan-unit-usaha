import requests
from app.config import config

def send_whatsapp(target: str, message: str):
    if not config.FONNTE_TOKEN: return
    try:
        requests.post(config.FONNTE_URL, headers={"Authorization": config.FONNTE_TOKEN}, 
                      data={"target": target, "message": message}, timeout=10)
    except Exception as e:
        print(f"Fonnte Error: {e}")