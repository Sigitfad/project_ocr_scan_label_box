import os         #mengakses dan mengelola file atau folder di sistem komputer
import sys        #mengakses informasi sistem dan pengaturan yang digunakan python
import io         #mengelola data sementara di memori seperti membaca atau menulis file
import re         #mencari atau memeriksa pola teks tertentu dalam sebuah tulisan
import psycopg2   #library PostgreSQL — menggantikan sqlite3
import cv2        #library untuk memproses gambar atau video, misalnya dari kamera
import base64     #mengubah data menjadi format teks agar mudah dikirim melalui web
import threading  #menjalankan beberapa proses secara bersamaan dalam satu program
import logging    #mencatat aktivitas, informasi, atau error yang terjadi pada program
from datetime import datetime   #mengambil dan mengelola tanggal serta waktu saat ini

from flask import Flask, render_template, request, jsonify, send_file, Response
# Flask : class utama untuk membuat aplikasi web berbasis Flask
# render_template : menampilkan file HTML dari folder templates ke browser
# request : mengambil data yang dikirim dari browser ke server (GET, POST, form, JSON, dll)
# jsonify : mengubah data Python (dict/list) menjadi format JSON untuk response API
# send_file : mengirim file dari server ke browser (contoh: gambar, excel, pdf, dll)
# Response : membuat response HTTP manual dengan isi, status, dan header yang bisa diatur sendiri

from flask_socketio import SocketIO, emit
# SocketIO : menambahkan komunikasi real-time (dua arah) antara server dan browser
# emit : mengirim data/event dari server ke client secara langsung (real-time)

#tentukan lokasi folder tempat app.py ini berada, lalu daftarkan ke python agar bisa import modul lain
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

#import pengaturan, fungsi database, export, dan utilitas dari file lain di proyek ini
from config import (
    APP_NAME, JIS_TYPES, DIN_TYPES, MONTHS, MONTH_MAP, CODE_TO_NUMBER,
    PG_CONFIG, FILE_DIR, IMAGE_DIR, EXCEL_DIR, ROI_OPTIONS
)
from database import setup_database, load_existing_data, load_all_today, delete_codes, cleanup_old_images, TABLE_JIS, TABLE_DIN, _table_for_preset, _get_conn, get_shift_for_time, SHIFT_RANGES, _shift_condition
from export import execute_export, cleanup_expired_excel
from utils import create_directories, get_available_cameras

#atur format log yang tampil di terminal
logging.basicConfig(
    level=logging.WARNING,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('comtypes').setLevel(logging.CRITICAL)
logging.getLogger('comtypes.client._code_cache').setLevel(logging.CRITICAL)
logging.getLogger('PIL').setLevel(logging.CRITICAL)
logging.getLogger('easyocr').setLevel(logging.CRITICAL)
logging.getLogger('socketio').setLevel(logging.CRITICAL)
logging.getLogger('engineio').setLevel(logging.CRITICAL)
logging.getLogger('database').setLevel(logging.CRITICAL)
logging.getLogger('ocr').setLevel(logging.CRITICAL)
logging.getLogger('export').setLevel(logging.CRITICAL)
logging.getLogger('utils').setLevel(logging.CRITICAL)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

#buat aplikasi web flask dan aktifkan fitur realtime (socketio)
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'gsbattery')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')


class AppState:  #class untuk menyimpan state global aplikasi yang diakses oleh threads berbeda
    def __init__(self):  #konstruktor untuk inisialisasi semua state variable
        self.logic              = None  #object DetectionLogic yang menjalankan proses OCR dan deteksi
        self.is_running         = False  #flag apakah proses kamera/deteksi sedang berjalan
        self.preset             = "JIS"  #preset standar baterai yang dipilih (JIS atau DIN)
        self.target_label       = ""  #label target session untuk pemberian identitas pada scan
        self.camera_index       = 0  #index kamera yang sedang digunakan
        self.edge_mode          = False  #flag untuk menampilkan mode edge detection
        self.roi_mode           = "Full Frame (No ROI)"  #mode ROI (region of interest) yang dipilih
        self.available_cameras  = []  #list kamera yang tersedia di sistem
        self.last_frame_b64     = None  #frame gambar terakhir dalam format base64 untuk streaming
        self.stream_lock        = threading.Lock()  #lock untuk protect akses concurrent ke last_frame_b64
        self.export_lock        = threading.Lock()  #guard atomik untuk protect akses ke export_in_progress
        self.export_in_progress = False  #flag apakah export Excel sedang berjalan
        self.export_cancelled   = False  #flag untuk cancel proses export yang sedang berjalan
        self.qty_plan           = 0  #jumlah target/rencana kuantitas untuk mode QTY Plan Excel
        self.ocr_reader         = None  #object EasyOCR reader yang sudah diinisialisasi
        self.ocr_ready          = threading.Event()  #event untuk signal bahwa OCR reader sudah siap
        #shift selalu otomatis dari jam real-time — tidak perlu disimpan di state

state = AppState()
create_directories()
setup_database()

def _expiry_scheduler():  #fungsi untuk thread daemon yang menjalankan cleanup file Excel expired dan image lama
    import time  #import time untuk sleep
    #jalankan cleanup pertama kali saat startup
    deleted = cleanup_expired_excel()  #panggil fungsi cleanup untuk hapus file Excel yang sudah melewati expiry date
    if deleted:  #jika ada file yang dihapus
        logger.info("[EXPIRY] Startup cleanup: %d file dihapus: %s", len(deleted), deleted)  #log info cleanup startup
        socketio.emit('excel_files_changed', {'deleted': deleted, 'reason': 'expired'})  #emit event ke browser jika ada perubahan file
    cleanup_old_images(minutes_to_keep=43200)  #cleanup image lama (lebih dari 30 hari) pada startup
    while True:  #loop infinite untuk pengecekan berkala
        #cek setiap 5 menit agar file expired cepat terhapus
        time.sleep(300)  #sleep 300 detik (5 menit)
        deleted = cleanup_expired_excel()  #cek dan hapus file Excel yang sudah expired
        if deleted:  #jika ada file yang dihapus
            logger.info("[EXPIRY] Auto-cleanup: %d file dihapus: %s", len(deleted), deleted)  #log info auto cleanup
            socketio.emit('excel_files_changed', {'deleted': deleted, 'reason': 'expired'})  #emit event ke browser
        #cleanup gambar lama setiap 6 jam (72 kali loop x 5 menit)
        #gunakan counter sederhana
        _expiry_scheduler._img_counter = getattr(_expiry_scheduler, '_img_counter', 0) + 1  #increment counter, init ke 0 jika belum ada
        if _expiry_scheduler._img_counter >= 72:  #cek apakah counter sudah mencapai 72 (6 jam)
            cleanup_old_images(minutes_to_keep=43200)  #cleanup image lama
            _expiry_scheduler._img_counter = 0  #reset counter

threading.Thread(target=_expiry_scheduler, daemon=True, name="ExpiryScheduler").start()

#AUTO-EXPORT 10 MENIT SEBELUM SHIFT BERAKHIR
#Export otomatis dijalankan 10 menit sebelum jam pergantian shift,
#agar file Excel siap sebelum shift berikutnya dimulai.
#Data yang diambil mencakup seluruh shift hingga detik saat export dijalankan.
#
#Jadwal export otomatis (server-time):
#  Shift 1 (07:00–15:59) → export dijalankan jam 15:50  (10 mnt sebelum 16:00)
#  Shift 2 (16:00–23:59) → export dijalankan jam 23:50  (10 mnt sebelum 00:00)
#  Shift 3 (00:00–06:59) → export dijalankan jam 06:50  (10 mnt sebelum 07:00)
#
#Event shift_change ke browser tetap dikirim TEPAT di jam pergantian (16:00 / 00:00 / 07:00).

#EXPORT_EARLY_MIN: berapa menit sebelum jam shift berakhir export dijalankan
EXPORT_EARLY_MIN = 10

def _subtract_minutes(h, m, minutes):  #fungsi helper untuk kurangi waktu dalam format (jam, menit)
    """Kurangi (h, m) sebesar <minutes> menit, kembalikan (h_baru, m_baru)."""
    total = h * 60 + m - minutes  #konversi jam:menit ke total menit, kemudian kurangi
    total = total % (24 * 60)  #wrap negatif dengan modulo 24*60 (misal 00:00 - 10 → 23:50)
    return divmod(total, 60)  #divide total dengan 60 untuk dapat jam dan menit, return sebagai tuple

#_SHIFT_END_MAP: key = (jam, menit) waktu export dijalankan (10 mnt lebih awal)
#value = (shift_num, start_time_shift, shift_change_hour, shift_change_min)
#  start_time_shift : awal shift (untuk query data)
#  shift_change_*   : jam pergantian sesungguhnya (untuk emit shift_change)
_SHIFT_END_MAP = {  #mapping waktu export ke informasi shift (dijalankan 10 menit sebelum shift berakhir)
    #(jam export, mnt export) : (shift, start_shift, jam_ganti, mnt_ganti)
    _subtract_minutes(7,  0, EXPORT_EARLY_MIN): (3, "00:00:00", 7,  0),  #shift 3 end: export jam 06:50, ganti 07:00
    _subtract_minutes(16, 0, EXPORT_EARLY_MIN): (1, "07:00:00", 16, 0),  #shift 1 end: export jam 15:50, ganti 16:00
    _subtract_minutes(0,  0, EXPORT_EARLY_MIN): (2, "16:00:00", 0,  0),  #shift 2 end: export jam 23:50, ganti 00:00
}
#hasil: {(6,50):(3,...), (15,50):(1,...), (23,50):(2,...)}

def _do_auto_export(shift_num, date_str, start_t, end_t):  #fungsi untuk jalankan export otomatis untuk shift tertentu
    """Jalankan export otomatis untuk shift yang akan segera berakhir.
    Dipanggil 10 menit sebelum jam pergantian shift.
    end_t = waktu aktual saat export dimulai (bukan batas akhir shift yang hardcoded),
    sehingga seluruh data yang sudah terekam ikut terambil.
    Setiap label yang diproses pada shift tersebut menghasilkan 1 file Excel terpisah."""
    import time as _time  #import time module dengan alias untuk sleep

    #--- Fix #3: gunakan Lock untuk atomik check+set export_in_progress ---
    #tunggu export manual selesai dulu (max 5 menit = 60 x 5 detik)
    for _ in range(60):  #iterasi 60 kali untuk cek lock
        with state.export_lock:  #acquire export lock untuk akses atomic
            if not state.export_in_progress:  #cek apakah export tidak sedang berjalan
                state.export_in_progress = True  #langsung klaim dalam Lock
                state.export_cancelled   = False  #reset flag cancel
                break  #break dari loop setelah klaim
        _time.sleep(5)  #sleep 5 detik sebelum retry
    else:  #loop selesai tanpa break (timeout)
        #loop selesai tanpa break → export_in_progress masih True setelah 5 menit
        logger.warning("[AUTO-EXPORT] Shift %d: export manual masih berjalan setelah 5 menit, skip.", shift_num)  #log warning
        return  #return tanpa jalankan export

    shift_label     = f"Shift {shift_num}"  #format label untuk shift yang sedang diproses
    date_range_desc = date_str  #deskripsi tanggal range untuk laporan

    logger.info("[AUTO-EXPORT] Memulai export otomatis %s tanggal %s ...", shift_label, date_str)  #log info mulai export

    def _run():  #nested function untuk jalankan export di thread terpisah
        try:  #try block untuk menangani error selama proses export
            #ambil daftar label unik yang diproses pada shift ini
            from database import TABLE_JIS, TABLE_DIN, _get_conn  #import database helpers
            conn = _get_conn()  #buat koneksi ke database
            try:  #try block untuk query database
                cur = conn.cursor()  #buat cursor untuk eksekusi query
                cur.execute(  #jalankan query untuk ambil daftar label unik di shift ini
                    f"SELECT DISTINCT target_session FROM {TABLE_JIS} "
                    f"WHERE timestamp BETWEEN %s AND %s AND target_session IS NOT NULL AND target_session != '' "
                    f"UNION "
                    f"SELECT DISTINCT target_session FROM {TABLE_DIN} "
                    f"WHERE timestamp BETWEEN %s AND %s AND target_session IS NOT NULL AND target_session != '' "
                    f"ORDER BY 1 ASC",
                    (f"{date_str} {start_t}", f"{date_str} {end_t}",  #parameter untuk rentang waktu shift
                     f"{date_str} {start_t}", f"{date_str} {end_t}")
                )
                labels = [row[0] for row in cur.fetchall() if row[0]]  #extract label dari hasil query
            except Exception as e:  #catch error saat query
                logger.error("[AUTO-EXPORT] %s: Gagal mengambil daftar label: %s", shift_label, e)  #log error
                labels = []  #set labels kosong jika ada error
            finally:  #cleanup koneksi
                conn.close()  #tutup koneksi database

            if not labels:  #cek apakah ada label yang ditemukan
                logger.info("[AUTO-EXPORT] %s: Tidak ada label/data untuk diekspor.", shift_label)  #log info tidak ada data
                socketio.emit('auto_export_done', {  #emit event ke browser
                    'ok': False, 'no_data': True,  #status gagal karena tidak ada data
                    'msg': f'Auto-Export {shift_label}: tidak ada data.',
                    'shift': shift_num,
                })
                return  #return dari fungsi jika tidak ada label

            exported_files = []  #list untuk menyimpan nama file yang berhasil diexport
            errors = []  #list untuk menyimpan label yang gagal diexport

            for label in labels:  #iterasi setiap label untuk diexport
                if state.export_cancelled:  #cek apakah export dibatalkan
                    break  #break dari loop jika dibatalkan

                sql_filter_label = (  #bangun SQL WHERE clause untuk filter label spesifik
                    f"WHERE timestamp BETWEEN '{date_str} {start_t}' "
                    f"AND '{date_str} {end_t}' "
                    f"AND target_session = '{label.replace(chr(39), chr(39)+chr(39))}'  "  #escape single quote
                )

                logger.info("[AUTO-EXPORT] %s — Mengekspor label: %s", shift_label, label)  #log info mulai export label

                result = execute_export(  #panggil fungsi execute_export untuk buat file Excel
                    sql_filter        = sql_filter_label,  #filter SQL untuk data label ini
                    date_range_desc   = date_range_desc,  #deskripsi tanggal untuk laporan
                    export_label      = label,  #nama label untuk nama file
                    current_preset    = state.preset,  #preset (JIS atau DIN)
                    progress_callback = lambda cur, tot, msg, lbl=label: socketio.emit(  #callback untuk update progress ke browser
                        'export_progress', {'current': cur, 'total': tot,
                                            'msg': f'[Auto-Export {shift_label} / {lbl}] {msg}'}
                    ),
                    cancel_flag   = state,  #pass state object untuk cek cancel flag
                    qty_plan      = state.qty_plan,  #jumlah qty plan
                    show_qty_plan = False,  #jangan tampilkan qty plan di auto-export
                )

                if result == "NO_DATA":  #cek hasil export
                    logger.info("[AUTO-EXPORT] %s / %s: Tidak ada data.", shift_label, label)  #log jika tidak ada data
                elif result and result.startswith("EXPORT_ERROR:"):  #cek jika ada error
                    logger.error("[AUTO-EXPORT] %s / %s: %s", shift_label, label, result)  #log error
                    errors.append(label)  #tambah label ke list error
                else:  #jika export berhasil
                    fn = os.path.basename(result)  #ambil nama file dari path
                    logger.info("[AUTO-EXPORT] %s / %s selesai → %s", shift_label, label, fn)  #log success
                    exported_files.append(fn)  #tambah nama file ke list exported files

            #--- Fix #6: sertakan errors dalam payload emit ---
            if exported_files:  #cek apakah ada file yang berhasil diexport
                files_summary = ", ".join(exported_files[:3])  #ambil 3 file pertama untuk summary
                if len(exported_files) > 3:  #jika lebih dari 3 file
                    files_summary += f" (+{len(exported_files)-3} lainnya)"  #tambah info file lainnya
                msg = f'Auto-Export {shift_label} selesai: {len(exported_files)} file'  #format message sukses
                if errors:  #jika ada error
                    msg += f' | {len(errors)} label GAGAL: {", ".join(errors[:3])}'  #tambah info error ke message
                socketio.emit('auto_export_done', {  #emit event sukses ke browser
                    'ok':       True,  #status ok
                    'filename': files_summary,  #daftar file yang diexport
                    'files':    exported_files,  #semua file yang diexport
                    'errors':   errors,  #daftar label yang gagal
                    'shift':    shift_num,  #nomor shift
                    'msg':      msg,  #message
                })
            else:  #jika tidak ada file yang berhasil
                err_detail = f': {", ".join(errors[:3])}' if errors else ''  #detail error jika ada
                socketio.emit('auto_export_done', {  #emit event gagal ke browser
                    'ok':     False,  #status gagal
                    'no_data': not errors,  #flag apakah tidak ada data (bukan error)
                    'errors':  errors,  #daftar label yang gagal
                    'shift':   shift_num,  #nomor shift
                    'msg':     (f'Auto-Export {shift_label}: {len(errors)} label gagal{err_detail}'  #message jika ada error
                                if errors else
                                f'Auto-Export {shift_label}: tidak ada data.'),  #message jika tidak ada data
                })

        except Exception as e:  #catch exception selama proses export
            logger.error("[AUTO-EXPORT] %s error: %s", shift_label, e)  #log error
            socketio.emit('auto_export_done', {  #emit event error ke browser
                'ok': False, 'msg': str(e), 'shift': shift_num,  #status error dengan message
            })
        finally:  #cleanup
            state.export_in_progress = False  #reset flag export selesai

    #--- Fix #2: jika Thread().start() gagal, pastikan flag di-reset ---
    try:  #try block untuk start thread
        threading.Thread(target=_run, daemon=True, name=f"AutoExport-Shift{shift_num}").start()  #start thread daemon untuk jalankan _run
    except Exception as e:  #catch exception jika gagal start thread
        logger.error("[AUTO-EXPORT] Gagal menjalankan thread export Shift %d: %s", shift_num, e)  #log error
        state.export_in_progress = False  #reset flag agar export berikutnya tidak terblokir
        socketio.emit('auto_export_done', {  #emit event error ke browser
            'ok': False, 'msg': f'Gagal memulai thread export: {e}', 'shift': shift_num,  #message error
        })


def _auto_export_scheduler():  #fungsi untuk thread daemon yang mengatur jadwal auto-export per shift
    """
    Thread yang tidur sampai 10 menit sebelum pergantian shift,
    lalu memicu export otomatis shift yang akan segera berakhir.

    Alur per siklus:
      1. Bangun 10 menit sebelum jam ganti shift (misal 15:50 untuk shift 1).
      2. Jalankan export — end_t query = detik saat export dimulai (bukan 15:59:59),
         sehingga data terkini ikut terambil meskipun masih dalam shift.
      3. Tunggu hingga jam ganti shift tepat, lalu emit shift_change ke browser.
      4. Sleep 65 detik anti-retrigger, lalu ulangi.

    Konstanta EXPORT_EARLY_MIN mengontrol berapa menit sebelum shift export dijalankan.
    """
    import time as _time  #import time module dengan alias

    #waktu export otomatis (sudah dikurangi EXPORT_EARLY_MIN), diurutkan
    EXPORT_KEYS = sorted(_SHIFT_END_MAP.keys())  #list waktu export dalam urutan: [(6,50), (15,50), (23,50)]

    #pemetaan jam ganti shift → nomor shift berikutnya (untuk emit shift_change)
    _NEXT_SHIFT_MAP = {  #mapping jam pergantian shift ke nomor shift berikutnya
        (7,  0): 1,  #07:00 → shift 1 mulai
        (16, 0): 2,  #16:00 → shift 2 mulai
        (0,  0): 3,  #00:00 → shift 3 mulai
    }

    while True:
        now = datetime.now()
        h, m, s = now.hour, now.minute, now.second
        now_minutes = h * 60 + m   #total menit dari tengah malam

        #--- 1. Cari waktu export berikutnya ---
        #Cek dulu apakah kita berada dalam toleransi ±2 mnt dari salah satu export time
        #(kasus app baru restart tepat setelah export time — hindari sleep 30+ jam)
        next_export = None
        for (eh, em) in EXPORT_KEYS:
            if abs((eh * 60 + em) - now_minutes) <= 2:
                next_export = (eh, em)   #kita sudah di momen export, langsung trigger
                break

        if next_export is None:
            for (eh, em) in EXPORT_KEYS:
                if now_minutes < eh * 60 + em:
                    next_export = (eh, em)
                    break
            if next_export is None:
                next_export = EXPORT_KEYS[0]   #wrap ke hari berikutnya

        eh, em = next_export
        export_total = eh * 60 + em

        #hitung detik sampai waktu export
        if export_total >= now_minutes:
            sleep_sec = (export_total - now_minutes) * 60 - s
        else:
            sleep_sec = (24 * 60 - now_minutes + export_total) * 60 - s

        sleep_sec = max(sleep_sec, 1)
        logger.debug(
            "[AUTO-EXPORT] Scheduler tidur %.0f detik — export berikutnya jam %02d:%02d "
            "(10 mnt sebelum pergantian shift).",
            sleep_sec, eh, em
        )
        _time.sleep(sleep_sec)

        #--- 2. Bangun — identifikasi shift yang akan segera berakhir ---
        now2 = datetime.now()
        trigger_key = (now2.hour, now2.minute)

        #toleransi ±2 menit untuk mengatasi overshoot kecil dari sleep
        info = _SHIFT_END_MAP.get(trigger_key)
        if not info:
            for key in EXPORT_KEYS:
                kh, km = key
                if abs((kh * 60 + km) - (now2.hour * 60 + now2.minute)) <= 2:
                    info = _SHIFT_END_MAP.get(key)
                    trigger_key = key
                    break

        if not info:
            logger.warning(
                "[AUTO-EXPORT] Tidak ada shift yang cocok untuk %02d:%02d — skip.",
                now2.hour, now2.minute
            )
            _time.sleep(65)
            continue

        shift_num, start_t, change_h, change_m = info

        #tentukan tanggal data berdasarkan shift
        #penting: export Shift 2 dijalankan jam 23:50 (BUKAN 00:00),
        #sehingga date_str = hari ini. Koreksi -1 hari hanya berlaku
        #jika export benar-benar dijalankan setelah tengah malam (change_h==0)
        #DAN jam saat ini sudah melewati tengah malam (now2.hour == 0).
        if change_h == 0 and now2.hour == 0:
            #export terlambat / overshoot melewati tengah malam → data kemarin
            from datetime import timedelta as _td
            date_str = (now2 - _td(days=1)).strftime('%Y-%m-%d')
        else:
            #export tepat waktu (termasuk Shift 2 jam 23:50) → data hari ini
            date_str = now2.strftime('%Y-%m-%d')

        #end_t query = detik saat ini (bukan batas shift), agar data terkini ikut terambil
        end_t = now2.strftime('%H:%M:%S')

        logger.info(
            "[AUTO-EXPORT] Memulai export Shift %d (data %s %s s/d %s, "
            "pergantian jam %02d:%02d).",
            shift_num, date_str, start_t, end_t, change_h, change_m
        )

        #jalankan export di thread terpisah (tidak blocking scheduler)
        _do_auto_export(shift_num, date_str, start_t, end_t)

        #--- 3. Tunggu hingga jam ganti shift tepat, lalu emit shift_change ---
        now3         = datetime.now()
        change_total = change_h * 60 + change_m
        now3_minutes = now3.hour * 60 + now3.minute

        if change_total > now3_minutes:
            wait_change = (change_total - now3_minutes) * 60 - now3.second
        else:
            #change_total <= now3_minutes: jam ganti ada di hari berikutnya (kasus 00:00)
            wait_change = (24 * 60 - now3_minutes + change_total) * 60 - now3.second

        if 0 < wait_change <= EXPORT_EARLY_MIN * 60 + 30:
            #tunggu sisa waktu sampai jam pergantian (maksimal EXPORT_EARLY_MIN menit + buffer)
            _time.sleep(wait_change)

        next_shift_num = _NEXT_SHIFT_MAP.get((change_h, change_m), 0)
        logger.info(
            "[SHIFT-CHANGE] Pergantian shift: Shift %d → Shift %d jam %02d:%02d",
            shift_num, next_shift_num, change_h, change_m
        )
        socketio.emit('shift_change', {
            'ended_shift': shift_num,
            'next_shift':  next_shift_num,
            'change_at':   datetime.now().strftime('%H:%M'),
        })

        #--- 4. Jeda anti-retrigger ---
        _time.sleep(65)


threading.Thread(target=_auto_export_scheduler, daemon=True, name="AutoExportScheduler").start()

def _init_ocr_reader():  #fungsi untuk inisialisasi dan load model EasyOCR
    import easyocr, numpy as np  #import easyocr dan numpy
    try:  #try block untuk cek CUDA availability
        import torch  #import torch untuk cek GPU
        _gpu = torch.cuda.is_available()  #cek apakah CUDA GPU tersedia
    except ImportError:  #jika torch tidak terinstall
        _gpu = False  #set GPU ke False
    logger.info("Memuat model EasyOCR (GPU=%s)...", "Ya" if _gpu else "CPU")  #log info mulai load model
    reader = easyocr.Reader(['en'], gpu=_gpu, verbose=False)  #buat EasyOCR reader untuk bahasa English
    try:  #try block untuk warmup model
        reader.readtext(np.zeros((32, 128, 3), dtype=np.uint8), detail=0)  #jalankan readtext dengan dummy image untuk warmup
    except Exception:  #jika warmup gagal
        pass  #abaikan error
    logger.info("Model EasyOCR Selesai Dimuat.")  #log info model selesai diload
    return reader  #kembalikan reader object

def _ocr_loader_thread():  #fungsi untuk thread daemon yang load OCR reader
    state.ocr_reader = _init_ocr_reader()  #inisialisasi OCR reader dan simpan ke state
    state.ocr_ready.set()  #set event bahwa OCR sudah ready

threading.Thread(target=_ocr_loader_thread, daemon=True).start()

def _init_detection_logic():  #fungsi untuk inisialisasi DetectionLogic dengan callback untuk Flask/SocketIO
    from ocr import DetectionLogic  #import DetectionLogic dari module ocr
    from PIL import Image  #import PIL Image (untuk kompatibilitas)

    class FakeSignal:  #class wrapper untuk mengganti PyQt Signal dengan callback function
        def __init__(self, callback):  #konstruktor dengan callback
            self._cb = callback  #simpan callback function
        def emit(self, *args):  #method emit untuk call callback
            try:  #try block untuk menjalankan callback
                self._cb(*args)  #panggil callback dengan arguments
            except Exception as e:  #catch exception dari callback
                logger.error("Signal emit error: %s", e)  #log error

    def on_frame_update(pil_image):  #callback untuk update frame dari kamera
        try:  #try block untuk encode frame
            buf = io.BytesIO()  #buat in-memory buffer
            pil_image.save(buf, format='JPEG', quality=75)  #save image ke buffer dengan format JPEG
            b64 = base64.b64encode(buf.getvalue()).decode('utf-8')  #encode buffer ke base64 string
            with state.stream_lock:  #acquire lock untuk thread-safe access
                state.last_frame_b64 = b64  #simpan frame base64 ke state
            socketio.emit('frame', {'img': b64})  #emit frame ke browser via SocketIO
        except Exception as e:  #catch exception
            logger.error("Frame update error: %s", e)  #log error

    def on_code_detected(message):  #callback untuk saat kode terdeteksi
        socketio.emit('scan_done', {})  #emit scan_done event
        now     = datetime.now()  #ambil waktu sekarang
        today   = now.date()  #ambil tanggal hari ini
        shift   = get_shift_for_time(now)  #ambil shift saat ini
        records = load_existing_data(today, state.preset, shift)  #load data deteksi hari ini
        socketio.emit('code_detected', {  #emit event code_detected ke browser
            'message': message,  #pesan hasil OCR
            'records': _serialize_records(records)  #data deteksi dalam format JSON
        })

    def on_camera_status(message, is_active):  #callback untuk status kamera
        socketio.emit('camera_status', {'message': message, 'active': is_active})  #emit status ke browser

    def on_data_reset():  #callback untuk reset data
        socketio.emit('data_reset', {})  #emit data_reset event ke browser

    def on_all_text(text_list):  #callback untuk daftar semua OCR text
        socketio.emit('ocr_text', {'texts': text_list})  #emit list text ke browser

    def on_scan_start():  #callback untuk mulai scan
        socketio.emit('scan_start', {})  #emit scan_start event ke browser

    def on_motion(detected):  #callback untuk motion detection
        socketio.emit('motion', {'detected': detected})  #emit motion event ke browser

    logic = DetectionLogic(  #buat instance DetectionLogic dengan callback signal
        FakeSignal(on_frame_update),  #frame update signal
        FakeSignal(on_code_detected),  #code detected signal
        FakeSignal(on_camera_status),  #camera status signal
        FakeSignal(on_data_reset),  #data reset signal
        FakeSignal(on_all_text),  #all text signal
        shared_reader=state.ocr_reader,  #pass OCR reader dari state
        scan_start_signal=FakeSignal(on_scan_start),  #scan start signal
        motion_signal=FakeSignal(on_motion),  #motion signal
    )
    return logic  #kembalikan DetectionLogic instance


def _serialize_records(records):  #fungsi untuk konversi list record ke format JSON-friendly
    return [{  #return list dictionary dengan key yang di-remap
        'id':      r.get('ID'),  #ID record
        'time':    r.get('Time', ''),  #waktu deteksi
        'code':    r.get('Code', ''),  #kode yang terdeteksi
        'type':    r.get('Type', ''),  #tipe preset (JIS atau DIN)
        'status':  r.get('Status', 'OK'),  #status OK atau Not OK (default OK)
        'target':  r.get('TargetSession', ''),  #target session/label
        'imgPath': r.get('ImagePath', ''),  #path gambar
    } for r in records]  #untuk setiap record dalam list

#route
@app.route('/')  #route untuk halaman utama
def index():  #fungsi untuk menampilkan halaman index HTML
    return render_template('index.html', app_name=APP_NAME)  #render template index.html dengan app name

@app.route('/api/ocr/ready')  #route API untuk cek apakah OCR reader ready
def api_ocr_ready():  #fungsi untuk return status OCR readiness
    return jsonify({'ready': state.ocr_ready.is_set()})  #return JSON dengan status OCR ready

@app.route('/api/cameras')  #route API untuk get daftar kamera tersedia
def api_get_cameras():  #fungsi untuk list semua kamera yang tersedia di sistem
    from config import MAX_CAMERAS  #import max cameras dari config
    cameras = get_available_cameras(MAX_CAMERAS)  #ambil daftar kamera dengan memanggil utility function
    state.available_cameras = cameras  #simpan daftar kamera ke state
    return jsonify({'cameras': [{'index': c['index'], 'name': c['name']} for c in cameras]})  #return JSON list kamera

@app.route('/api/camera/start', methods=['POST'])  #route API untuk start detection kamera
def api_camera_start():  #fungsi untuk mulai proses deteksi kamera
    if state.is_running:  #cek apakah deteksi sudah berjalan
        return jsonify({'ok': False, 'msg': 'Kamera sudah berjalan'})  #return error jika sudah berjalan

    data               = request.json or {}  #ambil JSON request data
    state.preset       = data.get('preset', 'JIS')  #ambil preset dari request (default JIS)
    state.target_label = data.get('label', '')  #ambil target label dari request
    state.camera_index = int(data.get('camera_index', 0))  #ambil camera index dari request
    state.edge_mode    = bool(data.get('edge_mode', False))  #ambil flag edge mode dari request
    state.roi_mode     = data.get('roi_mode', state.roi_mode)  #ambil ROI mode dari request

    if not state.ocr_ready.wait(timeout=60):  #tunggu OCR siap dengan timeout 60 detik
        return jsonify({'ok': False, 'msg': 'Model OCR belum siap, coba lagi sebentar.'})  #return error jika OCR belum ready

    state.logic = _init_detection_logic()  #inisialisasi DetectionLogic dengan callbacks
    state.logic.preset               = state.preset  #set preset pada logic
    state.logic.set_target_label(state.target_label)  #set target label pada logic
    state.logic.current_camera_index = state.camera_index  #set camera index pada logic
    state.logic.edge_mode            = state.edge_mode  #set edge mode pada logic
    state.logic.roi_mode             = state.roi_mode  #set ROI mode pada logic
    state.logic.daemon               = True  #set thread sebagai daemon thread

    state.is_running = True  #set flag is_running
    state.logic.start_detection()  #mulai deteksi di thread terpisah
    return jsonify({'ok': True})  #return success response

@app.route('/api/camera/stop', methods=['POST'])  #route API untuk stop detection kamera
def api_camera_stop():  #fungsi untuk hentikan proses deteksi kamera
    if not state.is_running:  #cek apakah deteksi sedang berjalan
        return jsonify({'ok': False, 'msg': 'Kamera tidak sedang berjalan'})  #return error jika tidak berjalan
    if state.logic:  #cek apakah logic object ada
        state.logic.stop_detection()  #stop deteksi pada logic
    state.is_running = False  #reset flag is_running
    state.logic      = None  #set logic ke None
    return jsonify({'ok': True})  #return success response

@app.route('/api/camera/settings', methods=['POST'])  #route API untuk update camera settings
def api_camera_settings():  #fungsi untuk update camera settings tanpa restart
    data               = request.json or {}  #ambil JSON request data
    state.preset       = data.get('preset', state.preset)  #update preset dari request
    state.target_label = data.get('label',  state.target_label)  #update target label dari request
    state.edge_mode    = bool(data.get('edge_mode',  state.edge_mode))  #update edge mode dari request
    state.roi_mode     = data.get('roi_mode', state.roi_mode)  #update ROI mode dari request

    if state.logic:  #jika logic sedang berjalan
        state.logic.preset     = state.preset  #update preset pada logic
        state.logic.edge_mode  = state.edge_mode  #update edge mode pada logic
        state.logic.roi_mode   = state.roi_mode  #update ROI mode pada logic
        state.logic.set_target_label(state.target_label)  #update target label pada logic

    return jsonify({'ok': True})  #return success response

@app.route('/api/scan/file', methods=['POST'])  #route API untuk scan file gambar
def api_scan_file():  #fungsi untuk proses scan file yang di-upload
    if state.is_running:  #cek apakah kamera live sedang berjalan
        return jsonify({'ok': False, 'msg': 'Hentikan kamera live terlebih dahulu'})  #return error jika live masih berjalan
    if 'file' not in request.files:  #cek apakah file ada di request
        return jsonify({'ok': False, 'msg': 'Tidak ada file yang diupload'})  #return error jika tidak ada file

    file = request.files['file']  #ambil file dari request
    if not file.filename:  #cek apakah filename ada
        return jsonify({'ok': False, 'msg': 'Nama file kosong'})  #return error jika filename kosong

    file.seek(0, 2)  #seek ke akhir file untuk cek ukuran
    size = file.tell()  #ambil ukuran file
    file.seek(0)  #seek kembali ke awal file
    if size > 10 * 1024 * 1024:  #cek apakah ukuran lebih dari 10 MB
        return jsonify({'ok': False, 'msg': 'Ukuran file maksimal 10 MB'})  #return error jika terlalu besar

    ext = os.path.splitext(file.filename)[1].lower()  #ambil extension file
    if ext not in ['.jpg', '.jpeg', '.png', '.bmp', '.webp']:  #cek apakah format file didukung
        return jsonify({'ok': False, 'msg': 'Format file tidak didukung'})  #return error jika format tidak didukung

    import tempfile  #import tempfile untuk buat temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:  #buat temp file dengan extension yang sesuai
        file.save(tmp.name)  #save uploaded file ke temp file
        tmp_path = tmp.name  #simpan path temp file

    if not state.logic:  #cek apakah logic sudah ada
        if not state.ocr_ready.wait(timeout=60):  #tunggu OCR siap
            return jsonify({'ok': False, 'msg': 'Model OCR belum siap'})  #return error jika OCR belum ready
        state.logic = _init_detection_logic()  #inisialisasi logic jika belum ada
        state.logic.preset     = state.preset  #set preset
        state.logic.edge_mode  = state.edge_mode  #set edge mode
        state.logic.roi_mode   = state.roi_mode  #set ROI mode
        state.logic.set_target_label(state.target_label)  #set target label

    result = state.logic.scan_file(tmp_path)  #jalankan scan pada file temp

    try:  #try block untuk cleanup temp file
        os.remove(tmp_path)  #hapus temp file
    except Exception:  #catch exception jika gagal delete
        pass  #abaikan error

    return jsonify({'ok': True, 'status': result})  #return success dengan hasil scan

@app.route('/api/data/today')  #route API untuk get data deteksi hari ini
def api_data_today():  #fungsi untuk ambil data deteksi hari ini sesuai preset dan shift
    now     = datetime.now()  #ambil waktu sekarang
    today   = now.date()  #ambil tanggal hari ini
    shift   = get_shift_for_time(now)  #ambil shift saat ini dari jam
    records = load_existing_data(today, state.preset, shift)  #load data dari database
    return jsonify({'records': _serialize_records(records), 'shift': shift})  #return data dalam format JSON

@app.route('/api/data/all_today')  #route API untuk get semua data hari ini (all shift)
def api_data_all_today():  #fungsi untuk ambil semua data deteksi hari ini tanpa filter shift
    now     = datetime.now()  #ambil waktu sekarang
    today   = now.date()  #ambil tanggal hari ini
    shift   = get_shift_for_time(now)  #ambil shift saat ini
    records = load_all_today(today, shift)  #load semua data hari ini dari database
    return jsonify({'records': _serialize_records(records), 'shift': shift})  #return data dalam format JSON

@app.route('/api/data/delete', methods=['POST'])  #route API untuk delete record
def api_data_delete():  #fungsi untuk delete record berdasarkan ID
    data = request.json or {}  #ambil JSON request data
    ids  = data.get('ids', [])  #ambil list ID dari request
    if not ids:  #cek apakah ada ID
        return jsonify({'ok': False, 'msg': 'Tidak ada ID yang diberikan'})  #return error jika tidak ada ID

    #gunakan logic.delete_codes() jika kamera aktif (update DB + list lokal sekaligus)
    #jika tidak, panggil database.delete_codes() langsung
    if state.logic:  #cek apakah logic sedang berjalan (kamera aktif)
        ok = state.logic.delete_codes(ids)  #delete via logic (update DB dan local list)
    else:  #jika logic tidak ada
        ok = delete_codes(ids, state.preset)  #delete langsung ke database

    if ok:  #cek apakah delete berhasil
        return jsonify({'ok': True})  #return success
    return jsonify({'ok': False, 'msg': 'Gagal menghapus record'})  #return error jika gagal

@app.route('/api/data/stats')  #route API untuk get statistik data hari ini
def api_data_stats():  #fungsi untuk hitung statistik OK dan Not OK
    now     = datetime.now()  #ambil waktu sekarang
    today   = now.date()  #ambil tanggal hari ini
    records = load_existing_data(today, state.preset, get_shift_for_time(now))  #load data hari ini
    ok      = sum(1 for r in records if r.get('Status') == 'OK')  #hitung jumlah OK
    nok     = sum(1 for r in records if r.get('Status') == 'Not OK')  #hitung jumlah Not OK
    return jsonify({'total': len(records), 'ok': ok, 'not_ok': nok})  #return statistik dalam JSON

#ambil daftar tanggal unik yang memiliki data scan di database
@app.route('/api/history/dates')  #route API untuk get daftar tanggal yang memiliki data
def api_history_dates():  #fungsi untuk ambil list tanggal unik dari database
    conn = _get_conn()  #buat koneksi ke database
    try:  #try block untuk query database
        cursor = conn.cursor()  #buat cursor
        cursor.execute(  #jalankan query untuk ambil tanggal unik
            f"SELECT DISTINCT SUBSTRING(timestamp, 1, 10) AS date FROM {TABLE_JIS} "
            f"UNION SELECT DISTINCT SUBSTRING(timestamp, 1, 10) FROM {TABLE_DIN} "
            f"ORDER BY date DESC"  #urutkan descending (tanggal terbaru dulu)
        )
        dates = [row[0] for row in cursor.fetchall()]  #extract tanggal dari hasil query
        return jsonify({'dates': dates})  #return JSON list tanggal
    except Exception as e:  #catch exception jika ada error
        return jsonify({'dates': [], 'error': str(e)})  #return error message
    finally:  #cleanup
        conn.close()  #tutup koneksi

#ambil semua data deteksi untuk tanggal tertentu (format YYYY-MM-DD)
@app.route('/api/history/by_date/<date_str>')  #route API untuk get data by tanggal
def api_history_by_date(date_str):  #fungsi untuk ambil data deteksi pada tanggal spesifik
    shift_param = request.args.get('shift', '0')  #ambil shift parameter dari query string (optional)
    try:  #try block untuk parse shift
        shift = int(shift_param)  #konversi shift ke integer
    except Exception:  #catch exception jika parse gagal
        shift = 0  #default shift ke 0 (no filter)

    conn = _get_conn()  #buat koneksi ke database
    try:  #try block untuk query database
        cursor = conn.cursor()  #buat cursor
        if shift and shift in SHIFT_RANGES:  #cek apakah shift valid dan ada di range
            cond = _shift_condition(date_str, shift)  #bangun condition berdasarkan shift
            cursor.execute(  #jalankan query dengan shift filter
                f"SELECT id, timestamp, code, preset, image_path, status, target_session FROM {TABLE_JIS} WHERE {cond} "
                f"UNION ALL SELECT id, timestamp, code, preset, image_path, status, target_session FROM {TABLE_DIN} WHERE {cond} "
                f"ORDER BY timestamp ASC"
            )
        else:  #jika tidak ada shift filter
            cursor.execute(  #jalankan query tanpa shift filter (seluruh hari)
                f"SELECT id, timestamp, code, preset, image_path, status, target_session FROM {TABLE_JIS} WHERE timestamp LIKE %s "
                f"UNION ALL SELECT id, timestamp, code, preset, image_path, status, target_session FROM {TABLE_DIN} WHERE timestamp LIKE %s "
                f"ORDER BY timestamp ASC",
                (date_str + '%', date_str + '%')  #parameter untuk LIKE query
            )
        rows    = cursor.fetchall()  #ambil semua hasil query
        records = [{'id': r[0], 'time': r[1], 'code': r[2], 'type': r[3], 'imgPath': r[4] or '', 'status': r[5] or 'OK', 'target': r[6] or r[2]} for r in rows]  #format hasil ke dict
        ok  = sum(1 for r in records if r['status'] == 'OK')  #hitung OK
        nok = sum(1 for r in records if r['status'] == 'Not OK')  #hitung Not OK
        return jsonify({'records': records, 'ok': ok, 'not_ok': nok, 'total': len(records)})  #return JSON
    except Exception as e:  #catch exception jika ada error
        return jsonify({'records': [], 'error': str(e)})  #return error message
    finally:  #cleanup
        conn.close()  #tutup koneksi

#ambil daftar label unik beserta statistik untuk tanggal tertentu
@app.route('/api/history/labels/<date_str>')  #route API untuk get daftar label unik dengan stats
def api_history_labels(date_str):  #fungsi untuk ambil list label unik dan statistik per label
    conn = _get_conn()  #buat koneksi database
    try:  #try block untuk query
        cursor = conn.cursor()  #buat cursor
        cursor.execute(  #jalankan query untuk ambil label, status, timestamp
            f"SELECT target_session, status, timestamp FROM {TABLE_JIS} WHERE timestamp LIKE %s "
            f"UNION ALL SELECT target_session, status, timestamp FROM {TABLE_DIN} WHERE timestamp LIKE %s "
            f"ORDER BY 1 ASC",
            (date_str + '%', date_str + '%')  #parameter untuk date filter
        )
        rows = cursor.fetchall()  #ambil hasil query
    except Exception as e:  #catch exception
        return jsonify({'labels': [], 'error': str(e)})  #return error
    finally:  #cleanup
        conn.close()  #tutup koneksi

    from collections import OrderedDict  #import OrderedDict untuk maintain order
    from datetime import datetime as _dt  #import datetime dengan alias

    #FIX: key = (shift_num, label) bukan hanya label.
    #Sebelumnya key hanya berupa label string, sehingga label yang sama
    #di shift berbeda (misal 105D31L Shift 1 dan 105D31L Shift 2) digabung
    #menjadi satu entry dengan shift dari scan PERTAMA saja.
    #Akibatnya Shift 2 tidak pernah muncul di panel meski sudah ada scan.
    label_map = OrderedDict()  #dictionary untuk simpan label stats
    for label, status, ts in rows:  #iterasi setiap row hasil query
        label = label or '—'  #gunakan '—' jika label kosong

        #tentukan shift dari timestamp baris ini
        shift_num = 0  #default shift 0
        if ts:  #jika ada timestamp
            try:  #try block untuk parse datetime
                ts_obj    = _dt.fromisoformat(ts) if isinstance(ts, str) else ts  #parse timestamp
                shift_num = get_shift_for_time(ts_obj)  #ambil shift dari timestamp
            except Exception:  #catch exception
                pass  #abaikan error

        key = (shift_num, label)  #buat key tuple dari shift dan label
        if key not in label_map:  #cek apakah key sudah ada
            label_map[key] = {'label': label, 'total': 0, 'ok': 0, 'not_ok': 0, 'shift': shift_num}  #buat entry baru
        label_map[key]['total'] += 1  #increment total
        if status == 'OK':  #cek status
            label_map[key]['ok'] += 1  #increment OK
        else:  #jika Not OK
            label_map[key]['not_ok'] += 1  #increment Not OK

    #urutkan: shift 1 → 2 → 3, lalu label alphabetical
    labels = sorted(label_map.values(), key=lambda x: (x['shift'], x['label']))  #sort by shift lalu label
    return jsonify({'labels': labels})  #return JSON list label

#ambil data deteksi untuk tanggal + shift + label tertentu
@app.route('/api/history/by_label/<date_str>/<path:label>')  #route API untuk get data by tanggal dan label
def api_history_by_label(date_str, label):  #fungsi untuk ambil data per label pada tanggal spesifik
    #shift opsional dari query string: ?shift=1 / ?shift=2 / ?shift=3
    #jika tidak ada atau 0, ambil semua data label di tanggal itu (behaviour lama)
    try:  #try block untuk parse shift
        shift = int(request.args.get('shift', 0))  #ambil shift dari query parameter
    except (ValueError, TypeError):  #catch exception jika parse gagal
        shift = 0  #default ke 0

    #bangun kondisi waktu berdasarkan shift
    if shift and shift in SHIFT_RANGES:  #cek apakah shift valid
        start_t, end_t = SHIFT_RANGES[shift]  #ambil time range dari shift
        params = (  #params untuk BETWEEN query
            f"{date_str} {start_t}", f"{date_str} {end_t}", label,
            f"{date_str} {start_t}", f"{date_str} {end_t}", label,
        )
        where_jis = f"timestamp BETWEEN %s AND %s AND target_session=%s"  #condition untuk JIS tabel
        where_din = f"timestamp BETWEEN %s AND %s AND target_session=%s"  #condition untuk DIN tabel
    else:  #jika tidak ada shift filter
        params    = (date_str + '%', label, date_str + '%', label)  #params untuk LIKE query
        where_jis = f"timestamp LIKE %s AND target_session=%s"  #condition untuk JIS tabel
        where_din = f"timestamp LIKE %s AND target_session=%s"  #condition untuk DIN tabel

    conn = _get_conn()  #buat koneksi database
    try:  #try block untuk query
        cursor = conn.cursor()  #buat cursor
        cursor.execute(  #jalankan query untuk ambil data label
            f"SELECT id, timestamp, code, preset, image_path, status, target_session FROM {TABLE_JIS} WHERE {where_jis} "
            f"UNION ALL SELECT id, timestamp, code, preset, image_path, status, target_session FROM {TABLE_DIN} WHERE {where_din} "
            f"ORDER BY timestamp ASC",
            params  #parameter untuk query
        )
        rows    = cursor.fetchall()  #ambil hasil query
        records = [{'id': r[0], 'time': r[1], 'code': r[2], 'type': r[3], 'imgPath': r[4] or '', 'status': r[5] or 'OK', 'target': r[6] or r[2]} for r in rows]  #format ke dict
        ok  = sum(1 for r in records if r['status'] == 'OK')  #hitung OK
        nok = sum(1 for r in records if r['status'] == 'Not OK')  #hitung Not OK
        return jsonify({'records': records, 'ok': ok, 'not_ok': nok, 'total': len(records)})  #return JSON
    except Exception as e:  #catch exception
        return jsonify({'records': [], 'error': str(e)})  #return error
    finally:  #cleanup
        conn.close()  #tutup koneksi

@app.route('/api/image/<path:filename>')  #route API untuk serve gambar
def api_serve_image(filename):  #fungsi untuk ambil dan serve gambar dari disk
    img_path = os.path.join(IMAGE_DIR, filename)  #bangun full path gambar
    if os.path.exists(img_path):  #cek apakah gambar ada di path
        return send_file(img_path, mimetype='image/jpeg')  #serve gambar jika ada

    basename = os.path.basename(filename)  #ambil hanya nama file dari path
    if os.path.exists(IMAGE_DIR):  #cek apakah directory image ada
        for date_entry in os.scandir(IMAGE_DIR):  #scan date directories
            if not date_entry.is_dir():  #skip jika bukan directory
                continue
            candidate = os.path.join(date_entry.path, basename)  #cek di date directory
            if os.path.exists(candidate):  #jika ada di date directory
                return send_file(candidate, mimetype='image/jpeg')  #serve gambar
            for label_entry in os.scandir(date_entry.path):  #scan label subdirectories
                if label_entry.is_dir():  #jika adalah directory
                    candidate = os.path.join(label_entry.path, basename)  #cek di label directory
                    if os.path.exists(candidate):  #jika ada di label directory
                        return send_file(candidate, mimetype='image/jpeg')  #serve gambar

    return jsonify({'error': 'Image not found'}), 404  #return 404 jika tidak ditemukan

@app.route('/api/export', methods=['POST'])  #route API untuk export data ke Excel
def api_export():  #fungsi untuk handle export Excel request
    if state.export_in_progress:  #cek apakah export sudah sedang berjalan
        return jsonify({'ok': False, 'msg': 'Export sedang berjalan'})  #return error jika sedang berjalan

    data          = request.json or {}  #ambil JSON request data
    date_range    = data.get('date_range', 'Today')  #ambil range tanggal (Today/Month/CustomDate)
    preset_filter = data.get('preset', 'Preset')  #ambil preset filter dari request
    label_filter  = data.get('label', 'All Label')  #ambil label filter dari request
    month_name    = data.get('month', '')  #ambil nama bulan untuk Month range
    year_val      = data.get('year', str(datetime.now().year))  #ambil tahun dari request
    start_date    = data.get('start_date', '')  #ambil tanggal mulai untuk CustomDate
    end_date      = data.get('end_date', '')  #ambil tanggal akhir untuk CustomDate
    from_history  = data.get('from_history', False)  #flag apakah request dari history panel
    #shift dari history panel — 0 berarti tidak difilter per shift
    hist_shift    = int(data.get('shift', 0)) if data.get('shift') else 0  #ambil shift dari history panel

    #bangun klausa WHERE untuk query SQL berdasarkan filter tanggal yang dipilih
    #catatan: di PostgreSQL, sql_filter ini disisipkan langsung ke string query di execute_export.
    #nilai tanggal di sini berasal dari server (bukan input user langsung), sehingga aman.
    #ambil shift otomatis dari jam saat ini (tidak perlu dikirim dari client)
    now_export = datetime.now()  #ambil waktu sekarang untuk export
    shift_val  = get_shift_for_time(now_export)  #ambil shift saat ini dari jam

    conditions = []  #list untuk menyimpan SQL WHERE conditions
    if date_range == 'Today':  #jika filter range adalah Today
        today_str = now_export.strftime('%Y-%m-%d')  #format tanggal hari ini
        #filter shift otomatis berdasarkan jam saat ini
        if shift_val and shift_val in SHIFT_RANGES:  #jika shift valid
            start_t, end_t = SHIFT_RANGES[shift_val]  #ambil time range dari shift
            conditions.append(  #tambah condition dengan BETWEEN
                f"timestamp BETWEEN '{today_str} {start_t}' AND '{today_str} {end_t}'"
            )
        else:  #jika shift tidak valid
            conditions.append(f"timestamp LIKE '{today_str}%'")  #gunakan LIKE untuk seluruh hari
    elif date_range == 'Month' and month_name:  #jika filter range adalah Month
        kode_bulan = MONTH_MAP.get(month_name, datetime.now().month)  #ambil kode bulan dari month name
        month_num  = CODE_TO_NUMBER.get(kode_bulan, datetime.now().month)  #konversi ke nomor bulan
        conditions.append(f"timestamp LIKE '{year_val}-{month_num:02d}%'")  #tambah condition untuk bulan-tahun
    elif date_range == 'CustomDate' and start_date and end_date:  #jika filter range adalah CustomDate
        if from_history and hist_shift and hist_shift in SHIFT_RANGES:  #jika dari history dengan shift tertentu
            #export dari history panel dengan shift tertentu:
            #filter hanya data dalam rentang waktu shift yang dipilih, bukan seharian penuh
            h_start_t, h_end_t = SHIFT_RANGES[hist_shift]  #ambil time range dari hist_shift
            conditions.append(  #tambah condition BETWEEN dengan shift time range
                f"timestamp BETWEEN '{start_date} {h_start_t}' AND '{end_date} {h_end_t}'"
            )
        else:  #jika custom date tanpa shift filter
            conditions.append(  #tambah condition untuk seluruh hari
                f"timestamp BETWEEN '{start_date} 00:00:00' AND '{end_date} 23:59:59'"
            )

    actual_preset = state.preset if preset_filter == 'Preset' else preset_filter  #tentukan preset untuk export

    if label_filter and label_filter not in ('All Label', 'Preset'):  #jika ada label filter
        safe_label = label_filter.replace("'", "''")  #escape single quote untuk SQL safety
        conditions.append(f"target_session = '{safe_label}'")  #tambah condition untuk label filter

    sql_filter = ("WHERE " + " AND ".join(conditions)) if conditions else ""  #bangun final SQL WHERE clause

    from_history_desc = from_history  #flag apakah export dari history panel
    date_range_desc   = date_range  #deskripsi date range untuk file dan report
    if date_range == 'Today':  #jika Today
        date_range_desc = now_export.strftime('%Y-%m-%d')  #gunakan tanggal format YYYY-MM-DD
    elif date_range == 'Month' and month_name:  #jika Month
        date_range_desc = f"{month_name}_{year_val}"  #gunakan format NamaBulan_TAHUN
    elif date_range == 'CustomDate':  #jika CustomDate
        if from_history_desc and start_date == end_date and start_date:  #jika tanggal sama (single day)
            date_range_desc = start_date  #gunakan single date
        else:  #jika range multiple days
            date_range_desc = f"{start_date}_to_{end_date}"  #gunakan format start_to_end

    state.export_in_progress = True  #set flag export sedang berjalan
    state.export_cancelled   = False  #reset flag cancel

    show_qty_plan = (  #tentukan apakah tampilkan qty plan di Excel
        (date_range == 'Today' and label_filter not in ('All Label', '', None)) or  #tampilkan jika Today dengan label spesifik
        (from_history_desc and label_filter not in ('All Label', '', None))  #atau jika dari history dengan label spesifik
    )

    def do_export():  #nested function untuk jalankan export di thread terpisah
        try:  #try block untuk jalankan export
            result = execute_export(  #panggil execute_export untuk buat file Excel
                sql_filter        = sql_filter,  #SQL WHERE clause untuk filter data
                date_range_desc   = date_range_desc,  #deskripsi date range untuk laporan
                export_label      = label_filter,  #label untuk nama file Excel
                current_preset    = actual_preset,  #preset untuk filter tabel
                progress_callback = lambda cur, tot, msg: socketio.emit(  #callback untuk update progress
                    'export_progress', {'current': cur, 'total': tot, 'msg': msg}
                ),
                cancel_flag   = state,  #pass state untuk cek cancel flag
                qty_plan      = data.get('qty_plan', state.qty_plan),  #qty plan dari request atau state
                show_qty_plan = show_qty_plan,  #flag untuk tampilkan qty plan
            )
            if result == "NO_DATA":  #cek hasil export
                socketio.emit('export_done', {'ok': False, 'no_data': True, 'msg': 'Tidak ada data!'})  #emit jika tidak ada data
            elif result and result.startswith("EXPORT_ERROR:"):  #cek jika ada error
                socketio.emit('export_done', {'ok': False, 'msg': result.replace("EXPORT_ERROR: ", "")})  #emit error message
            else:  #jika export berhasil
                fn = os.path.basename(result)  #ambil nama file dari path
                socketio.emit('export_done', {'ok': True, 'path': result, 'filename': fn})  #emit success dengan path dan filename
        except Exception as e:  #catch exception
            socketio.emit('export_done', {'ok': False, 'msg': str(e)})  #emit error message
        finally:  #cleanup
            state.export_in_progress = False  #reset export flag

    threading.Thread(target=do_export, daemon=True).start()  #start thread daemon untuk jalankan export
    return jsonify({'ok': True})  #return immediate response (export berjalan di background)

@app.route('/api/export/download/<path:filename>')  #route API untuk download file Excel
def api_export_download(filename):  #fungsi untuk serve Excel file untuk download
    filepath = os.path.join(THIS_DIR, EXCEL_DIR, filename)  #bangun full path ke Excel file
    if not os.path.exists(filepath):  #cek apakah file ada
        return jsonify({'error': 'File not found'}), 404  #return 404 jika tidak ada
    with open(filepath, 'rb') as f:  #buka file dalam mode binary
        file_data = f.read()  #baca seluruh file
    return Response(  #return response dengan file data
        file_data,  #isi response dengan file binary data
        headers={  #set header response
            'Content-Disposition': f'attachment; filename="{filename}"',  #force download dengan nama file
            'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  #MIME type Excel
        }
    )

@app.route('/api/export/cancel', methods=['POST'])  #route API untuk cancel export yang sedang berjalan
def api_export_cancel():  #fungsi untuk set cancel flag pada export
    if state.export_in_progress:  #cek apakah export sedang berjalan
        state.export_cancelled = True  #set cancel flag
        return jsonify({'ok': True})  #return success
    return jsonify({'ok': False, 'msg': 'Tidak ada export yang sedang berjalan'})  #return error jika tidak ada export

@app.route('/api/labels')  #route API untuk get daftar labels (JIS, DIN, Months)
def api_labels():  #fungsi untuk return label choices untuk UI
    return jsonify({'jis': JIS_TYPES, 'din': DIN_TYPES, 'months': MONTHS})  #return JSON list labels

@app.route('/api/shift', methods=['GET'])  #route API untuk get shift information
def api_get_shift():  #fungsi untuk return shift info dan mapping
    now          = datetime.now()  #ambil waktu sekarang
    current_auto = get_shift_for_time(now)  #ambil shift saat ini
    return jsonify({  #return JSON dengan shift info
        'shift':        current_auto,  #shift saat ini
        'current_auto': current_auto,  #shift saat ini (duplikat)
        'shifts': {  #mapping shift ke jam range
            '1': '07:00 – 15:59',  #shift 1 range
            '2': '16:00 – 23:59',  #shift 2 range
            '3': '00:00 – 06:59',  #shift 3 range
        }
    })

@app.route('/api/state')  #route API untuk get current application state
def api_state():  #fungsi untuk return current state ke UI
    return jsonify({  #return JSON dengan semua state
        'running':      state.is_running,  #apakah kamera sedang running
        'preset':       state.preset,  #preset saat ini (JIS atau DIN)
        'target_label': state.target_label,  #target label saat ini
        'camera_index': state.camera_index,  #camera index saat ini
        'edge_mode':    state.edge_mode,  #edge mode flag
        'qty_plan':     state.qty_plan,  #qty plan value
        'roi_mode':     state.roi_mode,  #ROI mode saat ini
        'shift':        get_shift_for_time(datetime.now()),  #shift saat ini
    })

@app.route('/api/roi_options')  #route API untuk get daftar ROI options
def api_roi_options():  #fungsi untuk return ROI options
    return jsonify({'roi_options': ROI_OPTIONS})  #return JSON list ROI options

@app.route('/api/qty_plan', methods=['POST'])  #route API untuk set qty plan value
def api_set_qty_plan():  #fungsi untuk update qty plan di state
    data = request.json or {}  #ambil JSON request data
    try:  #try block untuk parse qty_plan
        state.qty_plan = max(0, int(data.get('qty_plan', 0)))  #parse dan set qty_plan (min 0)
        return jsonify({'ok': True, 'qty_plan': state.qty_plan})  #return success dengan new value
    except (ValueError, TypeError):  #catch exception jika parse gagal
        return jsonify({'ok': False, 'msg': 'Nilai QTY Plan tidak valid'})  #return error

@app.route('/api/export/list')  #route API untuk list semua Excel file
def api_export_list():  #fungsi untuk return daftar Excel file dengan expiry info
    from export import _load_expiry_records, cleanup_expired_excel  #import helper dari export module
    from datetime import datetime as _dt  #import datetime dengan alias
    #hapus file expired dulu
    deleted = cleanup_expired_excel()  #jalankan cleanup untuk hapus file expired
    if deleted:  #jika ada file yang dihapus
        logger.info("[EXPIRY] /api/export/list: %d file expired dihapus: %s", len(deleted), deleted)  #log info
        socketio.emit('excel_files_changed', {'deleted': deleted, 'reason': 'expired'})  #emit event ke browser

    #baca REAL-TIME langsung dari folder file_excel
    _BASE_DIR   = os.path.dirname(os.path.abspath(__file__))  #ambil base directory
    excel_dir   = os.path.join(_BASE_DIR, EXCEL_DIR)  #ambil Excel directory path
    os.makedirs(excel_dir, exist_ok=True)  #buat directory jika belum ada

    #ambil semua file .xlsx yang benar-benar ada di folder
    actual_files = {  #dictionary untuk simpan file yang ada
        f: os.path.join(excel_dir, f)  #mapping filename ke full path
        for f in os.listdir(excel_dir)  #iterasi file di Excel directory
        if f.lower().endswith('.xlsx') and os.path.isfile(os.path.join(excel_dir, f))  #filter hanya .xlsx files
    }

    #cocokkan dengan expiry record
    records = _load_expiry_records()  #load expiry records dari JSON
    now     = _dt.now()  #ambil waktu sekarang
    files   = []  #list untuk menyimpan file info

    for fname, fpath in actual_files.items():  #iterasi setiap file yang ada
        #cari expiry dari records (cek berbagai variasi path)
        expired   = None  #variable untuk simpan expiry date
        sisa_jam  = None  #variable untuk simpan sisa jam
        for rec_path, rec_exp in records.items():  #iterasi records
            if os.path.basename(rec_path) == fname:  #cocokkan dengan basename
                expired = rec_exp  #ambil expiry date
                break  #break setelah ketemu
        if expired:  #jika ada expiry date
            try:  #try block untuk hitung sisa jam
                exp_dt   = _dt.strptime(expired, "%Y-%m-%d %H:%M:%S")  #parse expiry date
                diff_sec = (exp_dt - now).total_seconds()  #hitung selisih detik
                sisa_jam = int(diff_sec // 3600)  #konversi ke jam
            except Exception:  #catch exception
                sisa_jam = None  #set ke None jika error

        try:  #try block untuk ambil modification time
            mtime = _dt.fromtimestamp(os.path.getmtime(fpath)).strftime("%Y-%m-%d %H:%M:%S")  #ambil mtime dan format
        except Exception:  #catch exception
            mtime = None  #set ke None jika error

        files.append({  #tambah file info ke list
            'name':     fname,  #nama file
            'filepath': fpath,  #full path file
            'expired':  expired,  #expiry date
            'sisa_jam': sisa_jam,  #sisa jam sampai expired
            'mtime':    mtime,  #modification time
        })

    files.sort(key=lambda x: x['name'], reverse=True)  #sort by filename descending
    return jsonify({'files': files})  #return JSON list files

@app.route('/api/export/open-folder')  #route API untuk buka folder Excel dengan Explorer
def api_open_folder():  #fungsi untuk open file explorer di folder Excel
    import subprocess  #import subprocess untuk launch explorer
    filepath    = request.args.get('filepath', '').replace('/', os.sep)  #ambil filepath dari query string
    _BASE_DIR   = os.path.dirname(os.path.abspath(__file__))  #ambil base directory
    #selalu buka folder file_excel (EXCEL_DIR), bukan FILE_DIR
    folder_path = os.path.join(_BASE_DIR, EXCEL_DIR)  #tentukan folder path
    os.makedirs(folder_path, exist_ok=True)  #buat folder jika belum ada
    if filepath and os.path.exists(filepath):  #cek apakah filepath ada dan valid
        #buka folder sekaligus highlight/select file yang dipilih
        subprocess.Popen(f'explorer /select,"{filepath}"')  #open explorer dengan select file
    else:  #jika filepath tidak ada
        #buka folder file_excel tanpa select
        subprocess.Popen(f'explorer "{folder_path}"')  #open explorer folder
    return jsonify({'ok': True})  #return success

@app.route('/api/export/open-file')  #route API untuk open file Excel dengan aplikasi default
def api_open_file():  #fungsi untuk open Excel file dengan aplikasi default
    import subprocess  #import subprocess untuk launch file
    filepath = request.args.get('filepath', '').strip()  #ambil filepath dari query string
    filepath = filepath.replace('/', os.sep).replace('\\', os.sep)  #normalize path separator
    abs_path = os.path.abspath(filepath)  #convert ke absolute path
    #fallback: jika tidak ditemukan, coba cari berdasarkan nama file di EXCEL_DIR
    if not os.path.exists(abs_path):  #cek apakah file ada
        excel_dir = os.path.abspath(os.path.join(THIS_DIR, EXCEL_DIR))  #ambil Excel directory
        fname     = os.path.basename(abs_path)  #ambil nama file
        resolved  = os.path.join(excel_dir, fname)  #bangun resolved path di Excel directory
        if os.path.exists(resolved):  #cek apakah file ada di Excel directory
            abs_path = resolved  #gunakan resolved path
    if os.path.exists(abs_path):  #final check apakah file ada
        subprocess.Popen(f'start "" "{abs_path}"', shell=True)  #open file dengan aplikasi default
        return jsonify({'ok': True})  #return success
    return jsonify({'ok': False, 'msg': 'File tidak ditemukan'})  #return error jika tidak ketemu

@app.route('/api/export/delete', methods=['POST'])  #route API untuk delete Excel file
def api_export_delete():  #fungsi untuk hapus Excel file dan expiry record
    """Hapus file Excel beserta entri expiry-nya dari excel_expiry.json."""
    from export import _load_expiry_records, _save_expiry_records  #import expiry helpers
    data     = request.json or {}  #ambil JSON request data
    filepath = data.get('filepath', '').strip()  #ambil filepath dari request
    if not filepath:  #cek apakah filepath ada
        return jsonify({'ok': False, 'msg': 'Filepath tidak boleh kosong'})  #return error jika kosong

    #normalisasi path — terima forward slash maupun backslash
    filepath = filepath.replace('/', os.sep).replace('\\', os.sep)  #normalize path separator
    abs_path = os.path.abspath(filepath)  #convert ke absolute path

    #keamanan: pastikan file berada di dalam folder EXCEL_DIR
    excel_dir = os.path.abspath(os.path.join(THIS_DIR, EXCEL_DIR))  #ambil Excel directory
    if not abs_path.startswith(excel_dir + os.sep) and os.path.dirname(abs_path) != excel_dir:  #security check
        #fallback: coba resolve berdasarkan nama file saja
        fname    = os.path.basename(abs_path)  #ambil nama file
        resolved = os.path.join(excel_dir, fname)  #resolve path di Excel directory
        if os.path.exists(resolved):  #cek apakah resolved path ada
            abs_path = resolved  #gunakan resolved path
        else:  #jika tidak ada
            return jsonify({'ok': False, 'msg': 'Akses file di luar folder yang diizinkan'})  #return error

    #hapus file dari disk
    deleted_file = False  #flag untuk track apakah file dihapus
    if os.path.exists(abs_path):  #cek apakah file ada
        try:  #try block untuk delete
            os.remove(abs_path)  #hapus file
            deleted_file = True  #set flag
        except Exception as e:  #catch exception
            return jsonify({'ok': False, 'msg': f'Gagal menghapus file: {e}'})  #return error

    #hapus entri dari excel_expiry.json — cocokkan berdasarkan nama file
    fname   = os.path.basename(abs_path)  #ambil nama file
    records = _load_expiry_records()  #load expiry records
    keys_to_remove = [  #list untuk keys yang akan dihapus
        k for k in list(records.keys())  #iterasi semua keys
        if os.path.basename(k) == fname  #cek apakah basename sama
    ]
    for k in keys_to_remove:  #iterasi keys yang akan dihapus
        del records[k]  #delete key dari records
    if keys_to_remove:  #jika ada key yang dihapus
        _save_expiry_records(records)  #save records yang sudah update

    if deleted_file or keys_to_remove:  #cek apakah ada yang dihapus
        return jsonify({'ok': True})  #return success
    #file mungkin sudah terhapus sebelumnya — anggap ok jika tidak ada di folder
    return jsonify({'ok': False, 'msg': 'File tidak ditemukan di folder file_excel'})  #return error

#fungsi socketIO
@socketio.on('connect')  #event handler untuk client connect
def on_connect():  #fungsi untuk handle client connect
    now     = datetime.now()  #ambil waktu sekarang
    today   = now.date()  #ambil tanggal hari ini
    shift   = get_shift_for_time(now)  #ambil shift saat ini
    records = load_existing_data(today, state.preset, shift)  #load data deteksi hari ini
    emit('init_data', {  #emit init data ke client
        'records': _serialize_records(records),  #daftar record
        'running': state.is_running,  #status running
        'preset':  state.preset,  #preset saat ini
        'label':   state.target_label,  #target label saat ini
        'shift':   shift,  #shift saat ini
    })

@socketio.on('disconnect')  #event handler untuk client disconnect
def on_disconnect():  #fungsi untuk handle client disconnect
    pass  #tidak ada action khusus saat disconnect

#entry point
if __name__ == '__main__':  #cek apakah script dijalankan langsung
    print("=" * 30)  #print separator
    print(" Akses: http://localhost:5000")  #print access URL
    print("=" * 30)  #print separator
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)  #run Flask-SocketIO server