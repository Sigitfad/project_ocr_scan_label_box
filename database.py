import psycopg2   #library untuk koneksi dan operasi database PostgreSQL
import psycopg2.extras   #untuk DictCursor dan helper lainnya
import os    #untuk operasi hapus file gambar dari disk
import logging    #untuk mencatat log saat proses auto-cleanup berjalan
from datetime import datetime, timedelta #untuk format timestamp dan hitung selisih waktu
from config import PG_CONFIG, IMAGE_DIR  #konfigurasi koneksi PostgreSQL dan folder gambar dari config

_db_logger = logging.getLogger(__name__)  #logger khusus modul ini untuk mencatat error dan info operasi database

#nama tabel berdasarkan preset
TABLE_JIS = 'jis_detected'  #nama tabel untuk menyimpan hasil deteksi preset JIS
TABLE_DIN = 'din_detected'  #nama tabel untuk menyimpan hasil deteksi preset DIN

#kembalikan nama tabel yang sesuai berdasarkan preset string
def _table_for_preset(preset):
    return TABLE_DIN if (preset or '').upper() == 'DIN' else TABLE_JIS  #pilih tabel DIN jika preset adalah 'DIN', selain itu gunakan tabel JIS

#buat koneksi baru ke PostgreSQL menggunakan konfigurasi dari config.py
def _get_conn():
    return psycopg2.connect(**PG_CONFIG)  #buka dan kembalikan koneksi baru ke database PostgreSQL

#buat tabel untuk satu preset (JIS atau DIN) jika belum ada, termasuk migrasi kolom
def _ensure_table(cursor, table_name):
    cursor.execute(
        "SELECT to_regclass(%s)",
        (f'public.{table_name}',)
    )  #cek apakah tabel sudah ada di schema public
    exists = cursor.fetchone()[0]  #ambil hasil pengecekan, None jika tabel belum ada

    if exists is None:
        cursor.execute(f'''CREATE TABLE {table_name} (
                            id              SERIAL PRIMARY KEY,
                            timestamp       TEXT,
                            code            TEXT,
                            preset          TEXT,
                            image_path      TEXT,
                            status          TEXT DEFAULT 'OK',
                            target_session  TEXT
                        )''')  #buat tabel baru dengan semua kolom yang dibutuhkan jika belum ada
    else:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table_name,)
        )  #ambil daftar kolom yang sudah ada di tabel untuk keperluan migrasi
        columns = [col[0] for col in cursor.fetchall()]  #simpan nama kolom dalam list untuk pengecekan

        if 'status' not in columns:
            try:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN status TEXT DEFAULT 'OK'")  #tambah kolom status jika belum ada (migrasi schema)
                cursor.execute(f"UPDATE {table_name} SET status = 'OK' WHERE status IS NULL")  #isi nilai default 'OK' untuk baris yang kolom status-nya masih NULL
            except Exception:
                pass  #abaikan error jika kolom sudah ditambahkan oleh proses lain secara bersamaan

        if 'target_session' not in columns:
            try:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN target_session TEXT")  #tambah kolom target_session jika belum ada (migrasi schema)
                cursor.execute(f"UPDATE {table_name} SET target_session = code WHERE target_session IS NULL")  #isi target_session dengan nilai code untuk baris lama yang belum memiliki nilai
            except Exception:
                pass  #abaikan error jika kolom sudah ditambahkan oleh proses lain secara bersamaan

    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_timestamp ON {table_name}(timestamp)")  #buat index pada kolom timestamp untuk mempercepat query filter berdasarkan waktu
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_target ON {table_name}(target_session)")  #buat index pada kolom target_session untuk mempercepat query filter berdasarkan sesi

#untuk inisialisasi database: buat tabel jis_detected dan din_detected jika belum ada
def setup_database():
    conn = _get_conn()  #buka koneksi ke database
    try:
        cursor = conn.cursor()  #buat cursor untuk menjalankan perintah SQL
        _ensure_table(cursor, TABLE_JIS)  #pastikan tabel JIS sudah ada, buat jika belum
        _ensure_table(cursor, TABLE_DIN)  #pastikan tabel DIN sudah ada, buat jika belum
        conn.commit()  #simpan perubahan DDL ke database
    finally:
        conn.close()  #tutup koneksi meskipun terjadi error

#definisi shift:
#-shift 1: 07:00 – 15:59  (hari yang sama)
#-shift 2: 16:00 – 23:59  (hari yang sama)
#-shift 3: 00:00 – 06:59  (hari yang sama — dalam konteks tanggal scan)
#catatan: shift 3 dimulai tengah malam, sehingga tanggalnya tetap
#mengikuti date yang diquery.  Jika ingin "shift malam yang dimulai
#kemarin malam", gunakan current_date = kemarin dan shift=3.

SHIFT_RANGES = {
    1: ("07:00:00", "15:59:59"),   #shift 1: 07:00 – 15:59
    2: ("16:00:00", "23:59:59"),   #shift 2: 16:00 – 23:59
    3: ("00:00:00", "06:59:59"),   #shift 3: 00:00 – 06:59
}

def get_shift_for_time(dt: datetime) -> int:
    #Kembalikan nomor shift (1/2/3) berdasarkan objek datetime
    h = dt.hour  #ambil jam dari objek datetime untuk menentukan shift
    if 7 <= h < 16:   #07:00 – 15:59
        return 1  #masuk shift 1 jika jam berada di rentang pagi hingga sore
    elif 16 <= h < 24:  #16:00 – 23:59
        return 2  #masuk shift 2 jika jam berada di rentang sore hingga malam
    else:  #00:00 – 06:59
        return 3  #masuk shift 3 jika jam berada di rentang dini hari

def _shift_condition(date_str: str, shift: int) -> str:
    """
    Buat kondisi SQL PostgreSQL untuk filter berdasarkan tanggal + shift.
    Mengembalikan string kondisi yang bisa dimasukkan setelah WHERE/AND.
    """
    if shift not in SHIFT_RANGES:
        return f"timestamp LIKE '{date_str}%'"  #fallback ke filter tanggal saja jika nomor shift tidak dikenal
    start, end = SHIFT_RANGES[shift]  #ambil waktu mulai dan akhir shift dari dictionary
    return (
        f"timestamp BETWEEN '{date_str} {start}' "
        f"AND '{date_str} {end}'"
    )  #kembalikan kondisi BETWEEN untuk memfilter rekaman dalam rentang waktu shift yang ditentukan

#untuk mengambil semua data deteksi untuk tanggal tertentu dari database
def load_existing_data(current_date, preset='JIS', shift=0):
    detected_codes = []  #list untuk menampung hasil deteksi yang diambil dari database
    today_date_str = current_date.strftime("%Y-%m-%d")  #format tanggal menjadi string 'YYYY-MM-DD' untuk query SQL
    table          = _table_for_preset(preset)  #tentukan nama tabel berdasarkan preset yang diberikan

    conn = _get_conn()  #buka koneksi ke database
    try:
        cursor = conn.cursor()  #buat cursor untuk menjalankan perintah SQL
        if shift and shift in SHIFT_RANGES:
            cond = _shift_condition(today_date_str, shift)  #buat kondisi SQL untuk filter berdasarkan shift
            cursor.execute(
                f"SELECT id, timestamp, code, preset, image_path, status, target_session "
                f"FROM {table} WHERE {cond} ORDER BY timestamp ASC"
            )  #ambil semua rekaman untuk tanggal dan shift tertentu, diurutkan dari terlama ke terbaru
        else:
            cursor.execute(
                f"SELECT id, timestamp, code, preset, image_path, status, target_session "
                f"FROM {table} WHERE timestamp LIKE %s ORDER BY timestamp ASC",
                (today_date_str + '%',)
            )  #ambil semua rekaman untuk tanggal tertentu tanpa filter shift, diurutkan dari terlama ke terbaru
        for row in cursor.fetchall():
            detected_codes.append({
                'ID':            row[0],   #id unik rekaman di database
                'Time':          row[1],   #timestamp saat deteksi terjadi
                'Code':          row[2],   #kode yang berhasil dideteksi
                'Type':          row[3],   #jenis preset deteksi (JIS atau DIN)
                'ImagePath':     row[4],   #path file gambar hasil deteksi
                'Status':        row[5] if row[5] else 'OK',  #status rekaman, default 'OK' jika null
                'TargetSession': row[6] if row[6] else row[2]  #sesi target, fallback ke code jika null
            })
    except Exception as e:
        _db_logger.error("load_existing_data error: %s", e)  #catat error jika query gagal
    finally:
        conn.close()  #tutup koneksi meskipun terjadi error

    return detected_codes  #kembalikan list hasil deteksi yang sudah dibentuk menjadi dict

#untuk mengambil semua data hari ini dari kedua tabel (JIS + DIN) sekaligus
def load_all_today(current_date, shift=0):
    detected       = []  #list untuk menampung gabungan hasil deteksi dari kedua tabel
    today_date_str = current_date.strftime("%Y-%m-%d")  #format tanggal menjadi string 'YYYY-MM-DD' untuk query SQL

    conn = _get_conn()  #buka koneksi ke database
    try:
        cursor = conn.cursor()  #buat cursor untuk menjalankan perintah SQL
        if shift and shift in SHIFT_RANGES:
            cond = _shift_condition(today_date_str, shift)  #buat kondisi SQL untuk filter berdasarkan shift
            cursor.execute(
                f"SELECT id, timestamp, code, preset, image_path, status, target_session "
                f"FROM {TABLE_JIS} WHERE {cond} "
                f"UNION ALL "
                f"SELECT id, timestamp, code, preset, image_path, status, target_session "
                f"FROM {TABLE_DIN} WHERE {cond} "
                f"ORDER BY timestamp ASC"
            )  #gabungkan hasil dari kedua tabel dengan filter shift, diurutkan berdasarkan timestamp
        else:
            cursor.execute(
                f"SELECT id, timestamp, code, preset, image_path, status, target_session "
                f"FROM {TABLE_JIS} WHERE timestamp LIKE %s "
                f"UNION ALL "
                f"SELECT id, timestamp, code, preset, image_path, status, target_session "
                f"FROM {TABLE_DIN} WHERE timestamp LIKE %s "
                f"ORDER BY timestamp ASC",
                (today_date_str + '%', today_date_str + '%')
            )  #gabungkan hasil dari kedua tabel tanpa filter shift, diurutkan berdasarkan timestamp
        for row in cursor.fetchall():
            detected.append({
                'ID':            row[0],   #id unik rekaman di database
                'Time':          row[1],   #timestamp saat deteksi terjadi
                'Code':          row[2],   #kode yang berhasil dideteksi
                'Type':          row[3],   #jenis preset deteksi (JIS atau DIN)
                'ImagePath':     row[4],   #path file gambar hasil deteksi
                'Status':        row[5] if row[5] else 'OK',  #status rekaman, default 'OK' jika null
                'TargetSession': row[6] if row[6] else row[2]  #sesi target, fallback ke code jika null
            })
    except Exception as e:
        _db_logger.error("load_all_today error: %s", e)  #catat error jika query gagal
    finally:
        conn.close()  #tutup koneksi meskipun terjadi error

    return detected  #kembalikan list gabungan hasil deteksi dari kedua tabel

#untuk menghapus record dari database berdasarkan daftar ID
def delete_codes(record_ids, preset='JIS'):
    if not record_ids:
        return False  #langsung kembalikan False jika tidak ada ID yang diberikan

    table       = _table_for_preset(preset)  #tentukan nama tabel berdasarkan preset yang diberikan
    image_paths = []  #list untuk menampung path gambar yang akan dihapus dari disk

    conn = _get_conn()  #buka koneksi ke database
    try:
        cursor = conn.cursor()  #buat cursor untuk menjalankan perintah SQL
        cursor.execute(
            f"SELECT image_path FROM {table} WHERE id = ANY(%s)",
            (list(record_ids),)
        )  #ambil path gambar dari rekaman yang akan dihapus sebelum dihapus dari database
        image_paths = cursor.fetchall()  #simpan semua path gambar untuk dihapus dari disk setelah commit
        cursor.execute(
            f"DELETE FROM {table} WHERE id = ANY(%s)",
            (list(record_ids),)
        )  #hapus semua rekaman yang id-nya ada dalam daftar record_ids
        conn.commit()  #simpan perubahan penghapusan ke database
    except Exception as e:
        _db_logger.error("delete_codes error: %s", e)  #catat error jika penghapusan gagal
        conn.rollback()  #batalkan transaksi jika terjadi error agar data tetap konsisten
        return False  #kembalikan False sebagai tanda penghapusan gagal
    finally:
        conn.close()  #tutup koneksi meskipun terjadi error

    #hapus file gambar dari disk setelah koneksi ditutup
    for path_tuple in image_paths:
        image_path = path_tuple[0]  #ambil string path dari tuple hasil query
        if image_path and os.path.exists(image_path):
            try:
                os.remove(image_path)  #hapus file gambar dari disk jika file masih ada
            except Exception:
                pass  #abaikan error jika file tidak bisa dihapus (misal permission atau sudah terhapus)

    return True  #kembalikan True sebagai tanda penghapusan berhasil

#untuk menyimpan satu hasil deteksi baru ke tabel yang sesuai
def insert_detection(timestamp, code, preset, image_path, status, target_session):
    table  = _table_for_preset(preset)  #tentukan nama tabel berdasarkan preset yang diberikan
    new_id = None  #inisialisasi id baru, akan diisi setelah insert berhasil

    conn = _get_conn()  #buka koneksi ke database
    try:
        cursor = conn.cursor()  #buat cursor untuk menjalankan perintah SQL
        cursor.execute(
            f"INSERT INTO {table} (timestamp, code, preset, image_path, status, target_session) "
            f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (timestamp, code, preset, image_path, status, target_session)
        )  #sisipkan rekaman baru dan ambil id yang di-generate otomatis oleh database
        new_id = cursor.fetchone()[0]  #ambil id baru dari hasil RETURNING untuk dikembalikan ke pemanggil
        conn.commit()  #simpan rekaman baru ke database
    except Exception as e:
        _db_logger.error("insert_detection error: %s", e)  #catat error jika insert gagal
        conn.rollback()  #batalkan transaksi jika terjadi error agar data tetap konsisten
    finally:
        conn.close()  #tutup koneksi meskipun terjadi error

    return new_id  #kembalikan id rekaman baru, atau None jika insert gagal

#untuk menghitung total jumlah record di database (kedua tabel digabung)
def get_detection_count():
    total = 0  #inisialisasi total hitungan rekaman dari semua tabel
    conn  = _get_conn()  #buka koneksi ke database
    try:
        cursor = conn.cursor()  #buat cursor untuk menjalankan perintah SQL
        for tbl in (TABLE_JIS, TABLE_DIN):
            cursor.execute("SELECT to_regclass(%s)", (f'public.{tbl}',))  #cek apakah tabel ada sebelum melakukan COUNT
            if cursor.fetchone()[0]:
                cursor.execute(f"SELECT COUNT(*) FROM {tbl}")  #hitung jumlah rekaman di tabel jika tabel ada
                total += cursor.fetchone()[0]  #tambahkan jumlah rekaman tabel ini ke total keseluruhan
    except Exception as e:
        _db_logger.error("get_detection_count error: %s", e)  #catat error jika query count gagal
    finally:
        conn.close()  #tutup koneksi meskipun terjadi error

    return total  #kembalikan total jumlah rekaman dari semua tabel

#auto-delete: hapus file foto dari disk agar tidak terjadi penumpukan file
def cleanup_old_images(minutes_to_keep=129600):
    import shutil  #import shutil di sini untuk menghapus direktori secara rekursif
    cutoff          = datetime.now() - timedelta(minutes=minutes_to_keep)  #hitung batas waktu, file lebih lama dari ini akan dihapus
    cutoff_date_str = cutoff.strftime("%Y-%m-%d")  #format batas waktu menjadi string tanggal untuk perbandingan nama subfolder
    deleted_folders = 0  #penghitung subfolder yang berhasil dihapus
    deleted_files   = 0  #penghitung file gambar individual yang berhasil dihapus

    try:
        if os.path.exists(IMAGE_DIR):
            for entry in os.scandir(IMAGE_DIR):
                if not entry.is_dir():
                    continue  #lewati entri yang bukan direktori, hanya proses subfolder tanggal
                if entry.name <= cutoff_date_str:
                    try:
                        shutil.rmtree(entry.path)  #hapus seluruh subfolder tanggal beserta isinya secara rekursif
                        deleted_folders += 1  #catat satu subfolder berhasil dihapus
                    except Exception as e:
                        _db_logger.warning("Gagal hapus subfolder %s: %s", entry.path, e)  #catat warning jika subfolder gagal dihapus

        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")  #format batas waktu lengkap dengan jam untuk query SQL
        conn = _get_conn()  #buka koneksi ke database untuk mengambil path gambar lama
        try:
            cursor = conn.cursor()  #buat cursor untuk menjalankan perintah SQL
            cursor.execute(
                f"SELECT image_path FROM {TABLE_JIS} WHERE timestamp < %s AND image_path IS NOT NULL AND image_path != '' "
                f"UNION ALL SELECT image_path FROM {TABLE_DIN} WHERE timestamp < %s AND image_path IS NOT NULL AND image_path != ''",
                (cutoff_str, cutoff_str)
            )  #ambil semua path gambar lama dari kedua tabel yang melewati batas waktu cutoff
            old_records = cursor.fetchall()  #simpan semua path gambar lama untuk diproses penghapusannya
        finally:
            conn.close()  #tutup koneksi database setelah selesai mengambil data

        for (image_path,) in old_records:
            if image_path and os.path.exists(image_path) and os.path.isfile(image_path):
                parent = os.path.dirname(image_path)  #ambil direktori induk dari file gambar
                if os.path.abspath(parent) == os.path.abspath(IMAGE_DIR):
                    try:
                        os.remove(image_path)  #hapus file gambar yang berada langsung di IMAGE_DIR (bukan subfolder tanggal)
                        deleted_files += 1  #catat satu file gambar berhasil dihapus
                    except Exception as e:
                        _db_logger.warning("Gagal hapus foto %s: %s", image_path, e)  #catat warning jika file gambar gagal dihapus

        if deleted_folders > 0 or deleted_files > 0:
            _db_logger.info(
                "[AUTO-CLEANUP] Selesai: %d subfolder + %d foto dihapus (data > %d menit) | database tidak diubah",
                deleted_folders, deleted_files, minutes_to_keep
            )  #catat ringkasan hasil cleanup jika ada file atau folder yang berhasil dihapus

    except Exception as e:
        _db_logger.error("[AUTO-CLEANUP] Error saat cleanup foto: %s", e)  #catat error jika proses cleanup gagal secara keseluruhan