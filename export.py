import os       #untuk operasi file (membuat path, menghapus file thumbnail)
import re       #untuk sanitize nama label menjadi nama file yang valid
import json     #untuk membaca dan menulis file JSON penyimpanan data expired
import tempfile #untuk membuat file gambar sementara saat proses export
from datetime import datetime, timedelta    #untuk format timestamp dan nama file Excel dengan timestamp
from PIL import Image, ImageDraw, ImageFont #untuk memproses dan memberi label pada gambar
from config import PG_CONFIG, Resampling, EXCEL_DIR    #konfigurasi PostgreSQL dan metode resize gambar
from database import TABLE_JIS, TABLE_DIN, _table_for_preset, _get_conn  #nama tabel dan helper koneksi

#pandas dan SQLAlchemy diimport di sini — psycopg2 langsung tidak didukung oleh pd.read_sql_query,
#maka kita pakai SQLAlchemy sebagai jembatan agar tidak ada UserWarning saat export
import pandas as pd  #library untuk manipulasi dan analisis data dalam bentuk dataframe
from sqlalchemy import create_engine, text as sa_text  #library untuk membuat engine database dan query text aman

#ambil direktori tempat file ini berada sebagai base path untuk path relatif
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

#nama file JSON yang menyimpan daftar file Excel beserta tanggal expired-nya
EXPIRY_RECORD_FILE = os.path.join(_BASE_DIR, "data", "excel_expiry.json")  #path ke file JSON untuk tracking expiry tanggal

def _get_sqlalchemy_engine():  #fungsi untuk membuat dan mengembalikan SQLAlchemy engine instance
    #Buat SQLAlchemy engine dari PG_CONFIG — dipakai khusus untuk pd.read_sql_query
    cfg = PG_CONFIG  #ambil konfigurasi database dari file config
    url = (  #format URL connection string PostgreSQL dengan username, password, host, port, dan nama database
        f"postgresql+psycopg2://{cfg['user']}:{cfg['password']}"
        f"@{cfg['host']}:{cfg['port']}/{cfg['dbname']}"
    )
    return create_engine(url)  #mengembalikan SQLAlchemy engine yang sudah dikonfigurasi

def _load_expiry_records():  #fungsi untuk membaca data expiry records dari file JSON
    if not os.path.exists(EXPIRY_RECORD_FILE):  #cek apakah file JSON sudah ada
        return {}  #jika belum ada, kembalikan dictionary kosong
    try:
        with open(EXPIRY_RECORD_FILE, 'r') as f:  #buka file JSON dalam mode read
            return json.load(f)  #parse JSON dan kembalikan data sebagai dictionary
    except Exception:  #tangkap error jika ada kesalahan parsing JSON
        return {}  #kembalikan dictionary kosong jika terjadi error

def _save_expiry_records(records):  #fungsi untuk menyimpan data expiry records ke file JSON
    try:
        os.makedirs(os.path.join(_BASE_DIR, "data"), exist_ok=True)  #buat folder data jika belum ada
        with open(EXPIRY_RECORD_FILE, 'w') as f:  #buka file JSON dalam mode write
            json.dump(records, f, indent=2)  #tulis dictionary records ke file JSON dengan format rapi
    except Exception:  #tangkap error jika ada kesalahan menulis file
        pass  #abaikan error dan lanjutkan

def _register_expiry(filepath):  #fungsi untuk mendaftarkan file baru dengan tanggal expiry 30 hari ke depan
    records    = _load_expiry_records()  #ambil data expiry records yang sudah ada
    expired_at = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")  #hitung tanggal expiry 30 hari dari sekarang
    records[filepath] = expired_at  #tambah filepath baru ke records dengan tanggal expiry-nya
    _save_expiry_records(records)  #simpan records yang sudah diupdate ke file JSON

def cleanup_expired_excel():  #fungsi untuk menghapus file Excel yang sudah melampaui tanggal expiry
    """Hapus file Excel yang sudah melewati tanggal expiry.
    Mengembalikan list nama file yang berhasil dihapus."""
    records = _load_expiry_records()  #baca data expiry records dari file JSON
    if not records:  #cek apakah ada records
        return []  #jika tidak ada, kembalikan list kosong

    now       = datetime.now()  #ambil waktu sekarang untuk membandingkan dengan tanggal expiry
    to_delete = []  #list untuk menyimpan filepath yang perlu dihapus

    for filepath, expired_str in list(records.items()):  #iterasi semua file dan tanggal expiry-nya
        if not os.path.exists(filepath):  #cek apakah file masih ada di disk
            to_delete.append(filepath)  #tandai untuk dihapus dari records jika file sudah tidak ada
            continue  #lanjut ke iterasi berikutnya
        try:
            expired_at = datetime.strptime(expired_str, "%Y-%m-%d %H:%M:%S")  #parse string tanggal ke datetime object
            if now >= expired_at:  #cek apakah waktu sekarang sudah melewati tanggal expiry
                to_delete.append(filepath)  #tandai file untuk dihapus jika sudah expired
        except Exception:  #tangkap error jika ada kesalahan parsing tanggal
            to_delete.append(filepath)  #tandai file untuk dihapus jika ada error

    deleted_names = []  #list untuk menyimpan nama file yang berhasil dihapus
    for filepath in to_delete:  #iterasi semua file yang perlu dihapus
        fname = os.path.basename(filepath)  #ambil hanya nama file tanpa path lengkap
        if os.path.exists(filepath):  #cek sekali lagi apakah file masih ada
            try:
                os.remove(filepath)  #hapus file dari disk
                deleted_names.append(fname)  #catat nama file yang berhasil dihapus
            except Exception:  #tangkap error jika ada kesalahan menghapus file
                pass  #abaikan error dan lanjutkan
        records.pop(filepath, None)  #hapus entry file dari records dictionary

    if to_delete:  #jika ada file yang dihapus
        _save_expiry_records(records)  #simpan records yang sudah diupdate ke file JSON

    return deleted_names  #kembalikan list nama file yang berhasil dihapus

def execute_export(sql_filter="", date_range_desc="", export_label="", current_preset="",  #fungsi utama untuk mengekspor data deteksi ke file Excel dengan parameter filter, label, dan preset
                   progress_callback=None, cancel_flag=None, qty_plan=0, show_qty_plan=True):  #parameter untuk callback progress, flag cancel, dan konfigurasi qty plan
    """Fungsi utama: mengekspor data deteksi ke file Excel (.xlsx)."""

    def update_progress(current, total, message=""):  #fungsi nested untuk update progress melalui callback
        if progress_callback:  #cek apakah callback sudah diberikan
            progress_callback(current, total, message)  #panggil callback dengan current progress, total, dan pesan

    #sanitize label untuk nama file: ganti karakter yang tidak valid di Windows/Linux
    _safe_label = re.sub(r'[\\/:*?"<>|\s]', '_', export_label).strip('_') if export_label else 'NoLabel'  #hapus karakter invalid dari label dan ganti dengan underscore
    excel_filename      = f"Karton_{_safe_label}_{datetime.now().strftime('%Y%m%d')}.xlsx"  #format nama file Excel dengan tanggal dan label
    output_path         = os.path.join(_BASE_DIR, EXCEL_DIR, excel_filename)  #tentukan path lengkap untuk output file Excel
    temp_files_to_clean = []  #list untuk menyimpan temp files yang perlu dibersihkan nanti

    try:  #mulai try block untuk menangani error yang mungkin terjadi selama export
        update_progress(0,  100, "Membuka database...")  #update progress: tahap awal
        update_progress(5,  100, "Memeriksa struktur database...")  #update progress: cek struktur
        update_progress(10, 100, "Mengambil data dari database...")  #update progress: fetch data

        #bangun query SQL berdasarkan preset yang dipilih
        if current_preset in ('JIS', 'DIN'):  #cek apakah preset adalah JIS atau DIN
            table = _table_for_preset(current_preset)  #ambil nama tabel berdasarkan preset
            query = (  #buat query untuk mengambil data dari tabel single preset
                f"SELECT timestamp, code, preset, image_path, status, target_session "
                f"FROM {table} {sql_filter} ORDER BY timestamp ASC"
            )
        else:  #jika preset kosong atau tidak ada
            #gabungkan kedua tabel dengan UNION ALL untuk data dari kedua preset
            query = (  #buat query yang menggabungkan data dari kedua tabel JIS dan DIN
                f"SELECT timestamp, code, preset, image_path, status, target_session "
                f"FROM {TABLE_JIS} {sql_filter} "
                f"UNION ALL "
                f"SELECT timestamp, code, preset, image_path, status, target_session "
                f"FROM {TABLE_DIN} {sql_filter} "
                f"ORDER BY timestamp ASC"
            )

        #gunakan SQLAlchemy engine agar pd.read_sql_query tidak mengeluarkan UserWarning
        #psycopg2 langsung tidak didukung resmi oleh pandas — SQLAlchemy adalah cara yang benar
        #Fix #4: engine.dispose() dijamin dipanggil di finally agar tidak ada connection leak
        engine = _get_sqlalchemy_engine()  #buat SQLAlchemy engine untuk koneksi database
        try:  #mulai try block untuk menjalankan query
            with engine.connect() as sa_conn:  #buat koneksi database dengan context manager
                df = pd.read_sql_query(sa_text(query), sa_conn)  #jalankan query dan simpan hasil ke pandas dataframe
        finally:  #pastikan engine dilepas setelah selesai
            engine.dispose()  #lepas koneksi engine untuk mencegah connection leak

        if df.empty:  #cek apakah dataframe kosong
            update_progress(100, 100, "Tidak ada data")  #update progress dengan pesan tidak ada data
            return "NO_DATA"  #kembalikan status NO_DATA

        update_progress(15, 100, "Memproses data...")  #update progress: tahap pemrosesan data

        export_preset = current_preset if current_preset else "Mixed"  #tentukan preset yang akan ditampilkan di laporan
        if not current_preset and 'preset' in df.columns and not df['preset'].empty:  #jika tidak ada preset pilihan, coba tebak dari data
            unique_presets = df['preset'].unique()  #ambil nilai preset yang unik dari data
            if len(unique_presets) == 1:  #jika hanya ada satu preset unik
                export_preset = unique_presets[0]  #gunakan preset itu
            else:  #jika ada multiple preset
                export_preset = df['preset'].mode()[0] if not df['preset'].mode().empty else "Mixed"  #gunakan preset yang paling sering muncul atau "Mixed"

        label_display = export_label if (export_label and export_label != "All Label") else "All Labels"  #tentukan label yang akan ditampilkan di laporan

        update_progress(20, 100, "Menghitung statistik...")  #update progress: tahap hitung statistik
        qty_actual = len(df)  #hitung total jumlah record
        qty_ok     = len(df[df['status'] == 'OK'])  #hitung jumlah record dengan status OK
        qty_not_ok = len(df[df['status'] == 'Not OK'])  #hitung jumlah record dengan status Not OK

        START_ROW_DATA = 8 if show_qty_plan else 7  #tentukan baris awal untuk data berdasarkan apakah qty plan ditampilkan

        update_progress(25, 100, "Menyiapkan data untuk Excel...")  #update progress: tahap persiapan data
        df['timestamp'] = pd.to_datetime(df['timestamp'])  #konversi kolom timestamp menjadi datetime object
        df.insert(0, 'No', range(1, 1 + len(df)))  #tambah kolom nomor urut di awal dataframe
        df['Image'] = ""  #tambah kolom Image yang kosong untuk gambar nanti
        df.rename(columns={  #ubah nama kolom menjadi format laporan yang lebih readable
            'timestamp':      'Date/Time',
            'code':           'Scan Result',
            'preset':         'Standard',
            'image_path':     'Image Path',
            'status':         'Status',
            'target_session': 'Target Session'
        }, inplace=True)  #terapkan perubahan nama langsung pada dataframe
        df = df[['No', 'Image', 'Scan Result', 'Target Session', 'Date/Time', 'Status', 'Image Path', 'Standard']]  #atur ulang urutan kolom sesuai kebutuhan laporan

        update_progress(30, 100, "Membuat file Excel...")  #update progress: tahap membuat Excel
        sheet_name   = datetime.now().strftime("%Y-%m-%d")  #gunakan tanggal sekarang sebagai nama sheet
        _cancelled   = False   #flag lokal: cancel terjadi di dalam loop

        #Fix #1: gunakan context manager agar writer SELALU di-close,
        #bahkan jika exception terjadi di tengah penulisan baris.
        #Versi lama: writer.close() dipanggil manual — jika exception muncul
        #sebelum sampai ke writer.close(), file Excel tersimpan corrupt/kosong.
        with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:  #buat ExcelWriter dengan context manager untuk memastikan file ditutup dengan benar
            df.to_excel(writer, sheet_name=sheet_name, index=False, header=False, startrow=START_ROW_DATA)  #tulis data dataframe ke Excel tanpa header dan index, mulai dari baris START_ROW_DATA
            workbook  = writer.book  #ambil workbook object dari writer
            worksheet = writer.sheets[sheet_name]  #ambil worksheet object dari sheet_name

            update_progress(35, 100, "Mengatur format Excel...")  #update progress: tahap format Excel
            header_format          = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'font_color': 'white', 'bg_color': '#596CDA'})  #format untuk header baris (tebal, centered, warna biru)
            info_merge_format      = workbook.add_format({'bold': True, 'align': 'left',   'valign': 'vleft',  'font_size': 11})  #format untuk info baris yang di-merge
            center_format          = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})  #format untuk data cell normal (centered)
            datetime_center_format = workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm:ss', 'align': 'center', 'valign': 'vcenter', 'border': 1})  #format untuk datetime cell dengan format tanggal-waktu
            not_ok_format          = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1, 'bg_color': '#FF0000', 'font_color': '#FFFFFF'})  #format untuk status Not OK (merah dengan text putih)
            not_ok_datetime_format = workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm:ss', 'align': 'center', 'valign': 'vcenter', 'border': 1, 'bg_color': '#FF0000', 'font_color': '#FFFFFF'})  #format datetime untuk status Not OK

            worksheet.merge_range('A1:B1', f"Date : {date_range_desc}",  info_merge_format)  #tulis informasi tanggal di baris 1 (merged columns A-B)
            worksheet.merge_range('A2:B2', f"Type : {export_preset}",    info_merge_format)  #tulis informasi tipe/preset di baris 2
            worksheet.merge_range('A3:B3', f"Label : {label_display}",   info_merge_format)  #tulis informasi label di baris 3
            worksheet.merge_range('A4:B4', f"OK : {qty_ok}",             info_merge_format)  #tulis jumlah OK di baris 4
            worksheet.merge_range('A5:B5', f"Not OK : {qty_not_ok}",     info_merge_format)  #tulis jumlah Not OK di baris 5

            qty_text = f"QTY Actual : {qty_actual}"  #format text untuk menampilkan jumlah actual
            if show_qty_plan:  #cek apakah qty plan harus ditampilkan
                qty_plan_val    = qty_plan if qty_plan > 0 else qty_actual  #gunakan qty_plan atau qty_actual jika plan 0
                qty_plan_format = workbook.add_format({'bold': True, 'align': 'left', 'valign': 'vleft', 'font_size': 11, 'font_color': "#000000"})  #format untuk qty plan
                worksheet.merge_range('A6:B6', f"QTY Plan : {qty_plan_val}", qty_plan_format)  #tulis QTY Plan di baris 6
                worksheet.merge_range('A7:B7', qty_text, info_merge_format)  #tulis QTY Actual di baris 7
            else:  #jika qty plan tidak ditampilkan
                worksheet.merge_range('A6:B6', qty_text, info_merge_format)  #tulis QTY Actual di baris 6

            for col_num, value in enumerate(df.columns.values):  #iterasi semua kolom dataframe
                worksheet.write(START_ROW_DATA - 1, col_num, value, header_format)  #tulis header kolom di baris sebelum data dengan format header

            worksheet.set_column('A:A', 5)  #atur lebar kolom A (No) menjadi 5
            worksheet.set_column('B:B', 30)  #atur lebar kolom B (Image) menjadi 30 untuk gambar
            worksheet.set_column('C:C', 20)  #atur lebar kolom C (Scan Result) menjadi 20
            worksheet.set_column('D:D', 20)  #atur lebar kolom D (Target Session) menjadi 20
            worksheet.set_column('E:E', 25)  #atur lebar kolom E (Date/Time) menjadi 25
            worksheet.set_column('F:F', 10)  #atur lebar kolom F (Status) menjadi 10
            worksheet.set_column('G:G', 0, options={'hidden': True})  #sembunyikan kolom G (Image Path)
            worksheet.set_column('H:H', 0, options={'hidden': True})  #sembunyikan kolom H (Standard)

            update_progress(40, 100, "Menulis data ke Excel...")  #update progress: tahap menulis data
            total_rows = len(df)  #hitung total baris data
            for row_num, row_data in df.iterrows():  #iterasi setiap baris dari dataframe
                #cek cancel — break dari loop, writer tetap di-close oleh context manager
                if cancel_flag is not None and getattr(cancel_flag, 'export_cancelled', False):  #cek apakah ada permintaan cancel
                    _cancelled = True  #set flag cancel menjadi True
                    break  #keluar dari loop untuk menghentikan penulisan

                if row_num % 10 == 0 or row_num == total_rows - 1:  #update progress setiap 10 baris atau di baris terakhir
                    progress = 40 + int((row_num / total_rows) * 50)  #hitung persentase progress (40-90%)
                    update_progress(progress, 100, f"Memproses baris {row_num + 1} dari {total_rows}...")  #update progress dengan pesan

                excel_row       = row_num + START_ROW_DATA  #hitung nomor baris di Excel (data mulai dari START_ROW_DATA)
                image_path      = row_data['Image Path']  #ambil path gambar dari data
                status          = row_data['Status']  #ambil status dari data
                cell_format     = not_ok_format if status == 'Not OK' else center_format  #tentukan format cell berdasarkan status
                datetime_format = not_ok_datetime_format if status == 'Not OK' else datetime_center_format  #tentukan format datetime berdasarkan status

                try:  #coba tulis nomor dari data
                    worksheet.write(excel_row, 0, row_data['No'], cell_format)  #tulis nomor urut di kolom A
                except Exception:  #jika gagal, gunakan row_num + 1
                    worksheet.write(excel_row, 0, row_num + 1, cell_format)  #tulis nomor urut berdasarkan posisi row
                worksheet.write(excel_row, 1, '', cell_format)  #tulis cell kosong di kolom B untuk gambar (placeholder)

                if image_path and os.path.exists(str(image_path)):  #cek apakah gambar ada di disk
                    temp_dir           = tempfile.gettempdir()  #ambil path temp directory sistem
                    thumbnail_filename = f"app_temp_thumb_{os.getpid()}_{row_num}.png"  #buat nama file temp untuk thumbnail
                    thumbnail_path     = os.path.join(temp_dir, thumbnail_filename)  #buat path lengkap untuk temp thumbnail
                    temp_files_to_clean.append(thumbnail_path)  #tambahkan ke list untuk dibersihkan nanti

                    try:  #coba buat thumbnail dengan label
                        max_col_b_px          = int(30 * 7)  #hitung lebar maksimal untuk kolom B (30 karakter * 7 pixel)
                        target_row_max_height = 150  #tentukan tinggi maksimal untuk baris (dalam pixel)

                        img  = Image.open(image_path).convert("RGB")  #buka gambar dan konversi ke RGB format
                        draw = ImageDraw.Draw(img)  #buat object untuk menggambar text di gambar

                        try:  #coba load Arial font
                            font = ImageFont.truetype("arial.ttf", 30)  #load Arial font ukuran 30
                        except IOError:  #jika Arial tidak ada
                            font = ImageFont.load_default()  #gunakan default font

                        text_display = f"Detected: {row_data['Scan Result']}"  #format text dengan scan result
                        bbox = draw.textbbox((10, img.height - 50), text_display, font=font)  #hitung bounding box untuk text
                        draw.rectangle([bbox[0]-5, bbox[1]-5, bbox[2]+5, bbox[3]+5], fill=(0, 0, 0, 100))  #gambar background rectangle untuk text
                        draw.text((15, img.height - 50), text_display, fill=(255, 255, 0), font=font)  #gambar text label dengan warna kuning

                        img_full_w, img_full_h = img.size  #ambil ukuran gambar original (width, height)
                        img.save(thumbnail_path, format='PNG')  #simpan gambar dengan label ke temp path

                        scale_by_h    = target_row_max_height / img_full_h  #hitung scale factor berdasarkan tinggi
                        scale_by_w    = max_col_b_px / img_full_w  #hitung scale factor berdasarkan lebar
                        display_scale = min(scale_by_h, scale_by_w)  #gunakan scale factor yang lebih kecil untuk fit dalam cell

                        display_h = int(img_full_h * display_scale)  #hitung tinggi gambar setelah di-scale
                        display_w = int(img_full_w * display_scale)  #hitung lebar gambar setelah di-scale
                        worksheet.set_row(excel_row, display_h)  #set tinggi baris sesuai tinggi gambar

                        x_offset = max(0, (max_col_b_px - display_w) // 2 + 5)  #hitung offset x untuk center gambar horizontally
                        y_offset = max(0, (target_row_max_height - display_h) // 2)  #hitung offset y untuk center gambar vertically
                        worksheet.insert_image(excel_row, 1, thumbnail_path, {  #insert gambar ke cell (row, column=1 untuk kolom B)
                            'x_scale':  display_scale,  #scale horizontal
                            'y_scale':  display_scale,  #scale vertical
                            'x_offset': x_offset,  #offset horizontal untuk centering
                            'y_offset': y_offset  #offset vertical untuk centering
                        })  #parameter dict untuk positioning dan scaling gambar
                    except Exception:  #tangkap error jika ada kesalahan proses gambar
                        pass  #abaikan error dan lanjutkan (cell akan tetap kosong)

                worksheet.write(excel_row, 2, row_data['Scan Result'],    cell_format)  #tulis scan result di kolom C
                worksheet.write(excel_row, 3, row_data['Target Session'], cell_format)  #tulis target session di kolom D
                worksheet.write_datetime(excel_row, 4, row_data['Date/Time'], datetime_format)  #tulis datetime di kolom E dengan format datetime
                worksheet.write(excel_row, 5, row_data['Status'],         cell_format)  #tulis status di kolom F
                worksheet.write(excel_row, 6, row_data['Image Path'],     cell_format)  #tulis image path di kolom G (hidden)
                worksheet.write(excel_row, 7, row_data['Standard'],       cell_format)  #tulis standard di kolom H (hidden)

        #--- setelah blok 'with': writer sudah pasti tertutup di sini (context manager menjamin close)---

        #bersihkan temp files (selalu, baik cancel maupun normal)
        for t_path in temp_files_to_clean:  #iterasi semua temp file yang dibuat
            if os.path.exists(t_path):  #cek apakah temp file masih ada
                try:
                    os.remove(t_path)  #hapus temp file dari disk
                except Exception:  #tangkap error jika ada
                    pass  #abaikan error

        if _cancelled:  #cek apakah export dibatalkan
            #hapus file yang tidak sempurna akibat cancel
            if os.path.exists(output_path):  #cek apakah output file ada
                try:
                    os.remove(output_path)  #hapus file Excel yang tidak sempurna
                except Exception:  #tangkap error jika ada
                    pass  #abaikan error
            return "CANCELLED"  #kembalikan status CANCELLED

        update_progress(90, 100, "Mendaftarkan file...")  #update progress: tahap daftarkan file
        _register_expiry(output_path)  #daftarkan file dengan expiry date 30 hari

        update_progress(100, 100, "Export selesai!")  #update progress: 100% selesai
        return output_path  #kembalikan path file Excel yang sudah dibuat

    except Exception as e:  #catch error jika terjadi exception di try block
        update_progress(100, 100, f"Error: {e}")  #update progress dengan pesan error
        for t_path in temp_files_to_clean:  #iterasi semua temp file untuk dibersihkan
            if os.path.exists(t_path):  #cek apakah temp file ada
                try:
                    os.remove(t_path)  #hapus temp file
                except Exception:  #tangkap error jika ada
                    pass  #abaikan error
        return f"EXPORT_ERROR: {e}"  #kembalikan status error dengan detail exception