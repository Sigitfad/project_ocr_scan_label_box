"""
Jalankan script ini SEKALI sebelum menjalankan app.py di PC baru:

1. Membaca data dari type.db (SQLite)
2. Membuat tabel 'jis' dan 'din' di PostgreSQL jika belum ada
3. Memasukkan semua data ke PostgreSQL (skip jika sudah ada)
"""

import sqlite3  #library untuk membaca database SQLite
import sys  #library untuk operasi sistem (exit, argv, dll)
import os  #library untuk operasi file dan path

try:  #coba import psycopg2 untuk koneksi PostgreSQL
    import psycopg2  #library untuk koneksi dan operasi database PostgreSQL
except ImportError:  #jika psycopg2 tidak terinstall
    print("[ERROR] psycopg2 belum terinstall. Jalankan: pip install psycopg2-binary")
    sys.exit(1)  #exit script dengan error code 1

#ambil konfigurasi dari config.py
try:  #coba import konfigurasi database
    from config import PG_CONFIG  #import konfigurasi PostgreSQL (host, user, password, dbname, port)
except ImportError:  #jika config.py tidak ditemukan
    print("[ERROR] config.py tidak ditemukan. Pastikan migrate.py berada di folder yang sama dengan config.py")
    sys.exit(1)  #exit script dengan error code 1

#lokasi file SQLite
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))  #ambil path folder tempat script ini berada
SQLITE_DB = os.path.join(BASE_DIR, "data", "type.db")  #tentukan path lengkap ke file SQLite database


def baca_sqlite(db_path):  #fungsi untuk membaca data jis dan din dari file SQLite
    """Baca data jis dan din dari SQLite."""
    if not os.path.exists(db_path):  #cek apakah file SQLite ada di disk
        print(f"[ERROR] File SQLite tidak ditemukan: {db_path}")
        sys.exit(1)  #exit dengan error jika file tidak ada

    conn = sqlite3.connect(db_path)  #buka koneksi ke file SQLite
    cur  = conn.cursor()  #buat cursor untuk menjalankan query

    cur.execute("SELECT code FROM jis ORDER BY id")  #query untuk ambil semua code dari tabel jis
    jis_data = [r[0] for r in cur.fetchall()]  #extract code dari hasil query dan simpan ke list

    cur.execute("SELECT code FROM din ORDER BY id")  #query untuk ambil semua code dari tabel din
    din_data = [r[0] for r in cur.fetchall()]  #extract code dari hasil query dan simpan ke list

    conn.close()  #tutup koneksi SQLite
    return jis_data, din_data  #kembalikan dua list berisi data jis dan din

def buat_tabel_jika_belum_ada(cursor):  #fungsi untuk membuat tabel jis dan din di PostgreSQL jika belum ada
    """Buat tabel jis dan din jika belum ada di PostgreSQL."""
    for tabel in ("jis", "din"):  #iterasi untuk membuat kedua tabel (jis dan din)
        #jalankan query CREATE TABLE untuk setiap tabel
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {tabel} (
                id   SERIAL PRIMARY KEY,
                code TEXT NOT NULL UNIQUE
            )
        """)

def insert_data(cursor, tabel, data):  #fungsi untuk insert data ke tabel PostgreSQL dengan skip jika sudah ada
    """Insert data ke tabel, skip jika code sudah ada (ON CONFLICT DO NOTHING)."""
    inserted = 0  #counter untuk menghitung jumlah data yang berhasil diinsert
    skipped  = 0  #counter untuk menghitung jumlah data yang dilewati (sudah ada)
    for code in data:  #iterasi setiap code dari list data
        cursor.execute(  #jalankan query INSERT
            f"INSERT INTO {tabel} (code) VALUES (%s) ON CONFLICT (code) DO NOTHING",  #query INSERT dengan ON CONFLICT untuk skip jika code sudah ada
            (code,)  #parameter tuple berisi code yang akan diinsert
        )
        if cursor.rowcount == 1:  #cek apakah insert berhasil (rowcount = 1 berarti 1 baris ditambahkan)
            inserted += 1  #increment counter inserted
        else:  #jika rowcount != 1 berarti insert di-skip karena code sudah ada
            skipped += 1  #increment counter skipped
    return inserted, skipped  #kembalikan jumlah data yang inserted dan skipped

def main():  #fungsi utama untuk menjalankan proses migrasi dari SQLite ke PostgreSQL
    #1. baca SQLite
    print(f"\n[1/3] Membaca data dari {SQLITE_DB} ...")  #tampilkan info tahap 1: membaca SQLite
    jis_data, din_data = baca_sqlite(SQLITE_DB)  #panggil fungsi baca_sqlite untuk ambil data jis dan din
    print(f"      Ditemukan: {len(jis_data)} data JIS, {len(din_data)} data DIN")  #tampilkan berapa banyak data yang ditemukan

    #2.koneksi ke PostgreSQL
    print(f"\n[2/3] Menghubungkan ke PostgreSQL ({PG_CONFIG['host']}:{PG_CONFIG['port']}/{PG_CONFIG['dbname']}) ...")  #tampilkan info tahap 2: koneksi PostgreSQL
    try:  #coba buat koneksi ke PostgreSQL
        conn = psycopg2.connect(**PG_CONFIG)  #buat koneksi ke PostgreSQL dengan konfigurasi dari config.py
        conn.autocommit = False  #set autocommit ke False agar perlu commit manual
        cur  = conn.cursor()  #buat cursor untuk menjalankan query
        print("      Koneksi berhasil.")  #tampilkan pesan koneksi berhasil
    except psycopg2.OperationalError as e:  #catch error jika gagal koneksi
        print(f"\n[ERROR] Gagal konek ke PostgreSQL:\n{e}")  #tampilkan error message
        print("\nPastikan:")  #tampilkan checklist untuk troubleshoot
        print("  - PostgreSQL sudah berjalan")
        print("  - Database 'ocr_qc' sudah dibuat")
        print("  - Password di config.py sudah benar")
        sys.exit(1)  #exit dengan error code 1

    #3. buat tabel & insert data
    print(f"\n[3/3] Memasukkan data ke PostgreSQL ...")  #tampilkan info tahap 3: insert data
    try:  #coba buat tabel dan insert data
        buat_tabel_jika_belum_ada(cur)  #panggil fungsi untuk buat tabel jis dan din jika belum ada

        jis_in, jis_skip = insert_data(cur, "jis", jis_data)  #insert data jis dan ambil hasil insert dan skip count
        din_in, din_skip = insert_data(cur, "din", din_data)  #insert data din dan ambil hasil insert dan skip count

        conn.commit()  #commit semua perubahan ke database
        cur.close()  #tutup cursor
        conn.close()  #tutup koneksi PostgreSQL
    except Exception as e:  #catch error jika ada kesalahan saat insert
        conn.rollback()  #rollback transaksi jika ada error
        print(f"\n[ERROR] Gagal saat insert data:\n{e}")  #tampilkan error message
        sys.exit(1)  #exit dengan error code 1

    #hasil
    print("\n" + "=" * 55)  #tampilkan garis separator
    print("  MIGRASI SELESAI!")  #tampilkan pesan migrasi selesai
    print(f"  JIS : {jis_in} ditambahkan, {jis_skip} dilewati (sudah ada)")  #tampilkan summary JIS
    print(f"  DIN : {din_in} ditambahkan, {din_skip} dilewati (sudah ada)")  #tampilkan summary DIN
    print("=" * 55)  #tampilkan garis separator
    print("\nMenjalankan app.py\n")  #pesan akhir

if __name__ == "__main__":  #cek apakah script dijalankan langsung (bukan diimport sebagai module)
    main()  #jalankan fungsi main untuk memulai proses migrasi