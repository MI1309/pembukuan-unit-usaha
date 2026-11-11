# Deploy ke PythonAnywhere

## Upload dan Virtualenv

```bash
mkvirtualenv --python=/usr/bin/python3.12 agrinova-venv
workon agrinova-venv
pip install -r requirements.txt
```

Jika Python 3.12 tidak tersedia di akun, pilih versi yang tersedia dan pastikan kompatibel dengan paket-paket yang dipatok di `requirements.txt`.

## Web App

1. Buka tab **Web**, lalu buat web app dengan pilihan **Manual Configuration**. Pilih versi Python yang sama dengan virtualenv.
2. Atur virtualenv ke `agrinova-venv` dan working directory ke `/home/<username>/automation_wa_ai/pro-python`.
3. Buka file konfigurasi WSGI dari tautan pada tab Web. Ganti isinya dengan kode berikut, lalu sesuaikan username dan lokasi proyek:

```python
import os
import sys

project_home = "/home/<username>/automation_wa_ai/pro-python"
if project_home not in sys.path:
    sys.path.insert(0, project_home)
os.chdir(project_home)

from pythonanywhere_wsgi import application
```

4. Reload web app. Jika gagal, periksa error log di tab Web.
5. Atur URL webhook Fonnte menjadi `https://<username>.pythonanywhere.com/webhook`, lalu masukkan `WEBHOOK_SECRET` yang sama pada pengaturan webhook Fonnte.
6. Buka `https://<username>.pythonanywhere.com/` dan pastikan respons status kesehatan aplikasi muncul.

Titik masuk WSGI menggunakan `a2wsgi` untuk membungkus FastAPI. Scheduler backup tidak dijalankan di dalam web worker.

## Task Backup Harian

Tambahkan task harian pada tab **Tasks** PythonAnywhere dengan perintah berikut:

```bash
cd /home/<username>/automation_wa_ai/pro-python && /home/<username>/.virtualenvs/agrinova-venv/bin/python -m app.backup_task
```

Jadwalkan pada pukul 01.00 WIB. Jika jadwal di antarmuka menggunakan UTC, pilih pukul 18.00 UTC pada tanggal sebelumnya. Task ini membuat backup, menghapus backup yang lebih lama dari `BACKUP_KEEP_DAYS`, dan membersihkan data konfirmasi webhook yang sudah kedaluwarsa.

Sebelum mengaktifkan task harian, uji pembuatan satu salinan tanpa menjalankan pembersihan retensi:

```bash
cd /home/<username>/automation_wa_ai/pro-python && /home/<username>/.virtualenvs/agrinova-venv/bin/python -c 'from app.services.backup_service import create_backup_copy; print(create_backup_copy()["name"])'
```

Pastikan salinan tes muncul di Drive, baru aktifkan task harian. `app.backup_task` juga menghapus backup yang lebih lama dari `BACKUP_KEEP_DAYS`, jadi jangan gunakan perintah tersebut sebagai tes salinan pertama. Jika autentikasi Drive gagal, biasanya `drive_token.json` belum diunggah, akses OAuth kedaluwarsa, atau akun Google tidak memiliki izin ke folder tujuan.

## File dan Rahasia

Atur nilai `.env` di server, bukan di kode WSGI. Pastikan file privat berikut tersedia pada path yang dikonfigurasi:

- `credentials.json`: service-account key Sheets yang sudah diganti
- `drive_token.json`: refresh token OAuth Drive akun pribadi
- `.env`: spreadsheet ID, kredensial Fonnte/Groq, webhook secret, dan ID folder backup

Simpan file-file ini dengan aman. Jangan pernah mengirim isinya ke chat bantuan atau memasukkannya ke source control.
