import os     #operasi file, direktori, dan path untuk manajemen file gambar
import psycopg2  #untuk mengambil data JIS_TYPES dan DIN_TYPES dari database PostgreSQL
from PIL import Image  #PIL digunakan untuk mendeteksi versi resampling yang tersedia

#informasi Aplikasi
APP_NAME = "QC"    #nama aplikasi yang ditampilkan di ui

#direktori penyimpanan file
FILE_DIR  = "file"
IMAGE_DIR = os.path.join(FILE_DIR, "images")  #folder untuk menyimpan gambar hasil deteksi
EXCEL_DIR = os.path.join(FILE_DIR, "file_excel")  #folder untuk menyimpan file Excel hasil export

#konfgurasi postgresql
#bisa juga dibaca dari environment variable agar lebih aman (tidak hardcode).
PG_CONFIG = {
    "host":     os.environ.get("PG_HOST",     "localhost"), #host server PostgreSQL
    "port":     int(os.environ.get("PG_PORT", "5432")),  #port default PostgreSQL
    "dbname":   os.environ.get("PG_DBNAME",   "ocr_qc"),  #nama database
    "user":     os.environ.get("PG_USER",     "postgres"),  #username PostgreSQL
    "password": os.environ.get("PG_PASSWORD", "12345678"),  #password PostgreSQL
}

#konfigurasi database type (JIS/DIN) — menggunakan database/schema yang sama
#tabel: 'jis' dan 'din' berada di database yang sama (PG_CONFIG di atas)
#tidak perlu file type.db terpisah seperti sebelumnya.

#pengaturan kamera dan pemrosesan gambar
CAMERA_WIDTH  = 1280  #resolusi lebar frame dari kamera (px)
CAMERA_HEIGHT = 720   #resolusi tinggi frame dari kamera (px)
TARGET_WIDTH  = 640   #lebar gambar setelah di-resize untuk ocr (px)
TARGET_HEIGHT = 640   #tinggi gambar setelah di-resize untuk ocr (px)
BUFFER_SIZE   = 1     #jumlah frame yang di-buffer (1 = tanpa buffer berlebih)
SCAN_INTERVAL = 0.3   #jeda antar scan ocr dalam detik
MAX_CAMERAS   = 5     #maksimal kamera yang dicoba saat deteksi otomatis

#kompatibilitas resampling PIL
try:
    Resampling = Image.Resampling.LANCZOS  #pillow >= 9.1.0
except AttributeError:
    try:
        Resampling = Image.LANCZOS  #pillow lama
    except AttributeError:
        Resampling = Image.ANTIALIAS  #pillow sangat lama (fallback terakhir)

#preset dan pola regex deteksi
PRESETS = ["JIS", "DIN"]

#pola regex untuk mencocokkan kode baterai sesuai standar JIS dan DIN
PATTERNS = {
    "JIS": r"\b\d{2,3}[A-H]\d{2,3}[LR]?(?:\(S\))?\b",
    "DIN": r"(?:LBN\s*\d|LN[0-6](?:\s+\d{2,4}[A-Z]?(?:\s+ISS)?)?|\d{2,4}LN[0-6])"
}

#karakter yang diizinkan saat ocr membaca kode JIS (filter noise karakter lain)
ALLOWLIST_JIS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYLRS()'
#karakter yang diizinkan saat ocr membaca kode DIN
ALLOWLIST_DIN = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ '

#fungsi untuk memuat daftar types dari tabel PostgreSQL
def _load_types_from_db(table_name):
    result = ["Select Label . . ."]
    try:
        conn = psycopg2.connect(**PG_CONFIG)
        cur  = conn.cursor()
        cur.execute(f"SELECT code FROM {table_name} ORDER BY id")
        rows = cur.fetchall()
        conn.close()
        result.extend(row[0] for row in rows)
    except Exception:
        pass  #warning config diabaikan dari terminal
    return result

#daftar label baterai JIS — diambil dari tabel 'jis' di PostgreSQL
JIS_TYPES = _load_types_from_db("jis")

#daftar label baterai DIN — diambil dari tabel 'din' di PostgreSQL
DIN_TYPES = _load_types_from_db("din")

#pilihan roi (Region of Interest) untuk membatasi area scan OCR
ROI_OPTIONS = [
    "Full Frame (No ROI)",
    "Atas Kiri",
    "Atas Tengah",
    "Atas Kanan",
    "Bawah Kiri",
    "Bawah Tengah",
    "Bawah Kanan",
    "Tengah Frame",
    "Samping Tengah Kanan",
    "Samping Tengah Kiri",
]

#koordinat roi dari ukuran frame (x1, y1, x2, y2) dalam proporsi 0.0-1.0
ROI_COORDS = {
    "Full Frame (No ROI)":  (0.00, 0.00, 1.00, 1.00),
    "Atas Kiri":            (0.00, 0.00, 0.40, 0.40),
    "Atas Tengah":          (0.30, 0.00, 0.70, 0.40),
    "Atas Kanan":           (0.60, 0.00, 1.00, 0.40),
    "Bawah Kiri":           (0.00, 0.60, 0.40, 1.00),
    "Bawah Tengah":         (0.30, 0.60, 0.70, 1.00),
    "Bawah Kanan":          (0.60, 0.60, 1.00, 1.00),
    "Tengah Frame":         (0.25, 0.25, 0.75, 0.75),
    "Samping Tengah Kiri":  (0.00, 0.25, 0.40, 0.75),
    "Samping Tengah Kanan": (0.60, 0.25, 1.00, 0.75),
}

#daftar nama bulan dalam bahasa Indonesia untuk filter export (ditampilkan di UI)
MONTHS = [
    "Januari", "Februari", "Maret",    "April",
    "Mei",     "Juni",     "Juli",     "Agustus",
    "September","Oktober", "November", "Desember"
]

#pemetaan nama bulan (bahasa Indonesia) ke kode perusahaan
#Oktober = 0, November = A, Desember = B sesuai aturan perusahaan
MONTH_MAP = {
    "Januari":   1,   "Februari":  2,   "Maret":    3,   "April":   4,
    "Mei":       5,   "Juni":      6,   "Juli":     7,   "Agustus": 8,
    "September": 9,   "Oktober":   0,   "November": "A", "Desember":"B"
}

#konversi kode perusahaan ke angka integer untuk query SQL PostgreSQL
#kode 0=Oktober, A=November, B=Desember diterjemahkan ke angka bulan yang benar
CODE_TO_NUMBER = {
    1: 1,  2: 2,  3: 3,  4: 4,
    5: 5,  6: 6,  7: 7,  8: 8,
    9: 9,
    0:  10,   # Oktober
    "A": 11,  # November
    "B": 12,  # Desember
}