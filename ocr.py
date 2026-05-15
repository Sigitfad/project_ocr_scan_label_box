import cv2         #opencv untuk membaca kamera, memproses frame, dan menyimpan gambar
import easyocr     #library ocr untuk membaca teks dari gambar
import re          #regex untuk mencocokkan pola kode jis/din
import os          #operasi file/direktori untuk menyimpan gambar dan file excel
import time        #mengatur interval scan dan timestamp
import threading   #menjalankan deteksi dan scan di background thread
import logging     #untuk mencatat pesan debug tanpa menampilkannya di terminal
import atexit      #mendaftarkan fungsi cleanup saat program ditutup
import numpy as np #operasi array/matriks untuk pemrosesan gambar
from datetime import datetime #untuk mendapatkan tanggal saat ini dan format timestamp
from difflib import SequenceMatcher  #untuk menghitung kemiripan string saat pencocokan kode
from PIL import Image #pillow untuk manipulasi gambar dan konversi format untuk ui

_logger = logging.getLogger(__name__)

from config import (  #mengimpor konfigurasi dari file config.py
    IMAGE_DIR, EXCEL_DIR, PATTERNS, ALLOWLIST_JIS, ALLOWLIST_DIN, DIN_TYPES,
    CAMERA_WIDTH, CAMERA_HEIGHT, TARGET_WIDTH, TARGET_HEIGHT, BUFFER_SIZE,
    MAX_CAMERAS, SCAN_INTERVAL, JIS_TYPES, ROI_COORDS
)
from utils import ( #mengimpor fungsi-fungsi utilitas dari file utils.py
    fix_common_ocr_errors, convert_frame_to_binary,
    create_directories, apply_edge_detection
)
from database import ( #mengimpor fungsi-fungsi untuk mengelola database dari file database.py
    setup_database, load_existing_data, insert_detection
)

#kelas utama: menjalankan loop kamera dan proses ocr di thread terpisah
#mewarisi threading.Thread agar bisa berjalan paralel dengan UI/server
class DetectionLogic(threading.Thread):

    def __init__(self, update_signal, code_detected_signal, camera_status_signal, data_reset_signal, all_text_signal=None, shared_reader=None, scan_start_signal=None, motion_signal=None):
        super().__init__()
        #sinyal-sinyal untuk berkomunikasi dengan ui/frontend (menggunakan FakeSignal di mode web)
        self.update_signal = update_signal               #kirim frame terbaru ke ui
        self.code_detected_signal = code_detected_signal #kirim notifikasi kode terdeteksi
        self.camera_status_signal = camera_status_signal #kirim status kamera (aktif/mati)
        self.data_reset_signal = data_reset_signal       #kirim sinyal saat data direset harian
        self.all_text_signal = all_text_signal           #kirim semua teks hasil ocr ke ui
        self.scan_start_signal = scan_start_signal       #kirim sinyal saat ocr mulai berjalan
        self.motion_signal = motion_signal               #kirim status motion detection ke ui

        self.running = False      #flag kontrol loop utama kamera
        self.cap = None           #objek VideoCapture opencv
        self.preset = "JIS"       #preset aktif (JIS atau DIN)
        self.last_scan_time = 0   #waktu scan terakhir (untuk throttle interval scan)
        self.scan_interval = SCAN_INTERVAL  #jeda minimum antar scan (detik)
        self.target_label = ""       #label target yang sedang dipantau (kode lengkap, untuk display/session)
        self.target_label_compare = ""  #bagian JIS/DIN yang diekstrak untuk komparasi dengan hasil ocr

        self.current_camera_index = 0      #index kamera yang digunakan
        self.scan_lock = threading.Lock()  #lock untuk mencegah scan berjalan bersamaan
        self.temp_files_on_exit = []       #daftar file temp untuk dibersihkan saat exit

        self.edge_mode = False   #mode deteksi tepi (edge detection aktif/tidak)
        self.roi_mode = "Full Frame (No ROI)"  #mode roi aktif (nama dari ROI_OPTIONS)
        self.current_date = datetime.now().date()  #tanggal hari ini untuk reset data harian

        #struktur: images/tanggal/label/karton_xxx.jpg
        self.today_date_folder = os.path.join(IMAGE_DIR, self.current_date.strftime("%Y-%m-%d"))
        os.makedirs(self.today_date_folder, exist_ok=True)

        #cache subfolder per label: { 'LABEL': 'path/ke/subfolder/label' }
        #subfolder label dibuat sekali saat label pertama kali discan, lalu disimpan di sini
        self.label_folders = {}

        #cek duplikat dari loop list ribuan record menjadi lookup dictionary
        #struktur: { 'KODE': timestamp_float } hanya simpan waktu scan terakhir per kode
        self.recent_scans = {}  #dictionary: kode -> time.time() saat terakhir scan

        self.TARGET_WIDTH = TARGET_WIDTH #lebar target untuk frame yang dikirim ke ui
        self.TARGET_HEIGHT = TARGET_HEIGHT #tinggi target untuk frame yang dikirim ke ui
        self.patterns = PATTERNS #pola regex untuk kode JIS/DIN

        setup_database() #inisialisasi database jika belum ada (fallback jika dipanggil standalone)
        self.detected_codes = load_existing_data(self.current_date, self.preset) #muat data deteksi hari ini

        #ocr reader
        #jika shared_reader diberikan (mode web/Flask), gunakan langsung tanpa load ulang.
        if shared_reader is not None:
            self.reader = shared_reader
        else:
            try:
                import torch
                _gpu_available = torch.cuda.is_available()
            except ImportError:
                _gpu_available = False
            self.reader = easyocr.Reader(['en'], gpu=_gpu_available, verbose=False)

        #CLAHE: metode peningkatan kontras lokal untuk membantu ocr di kondisi cahaya buruk
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

        #daftarkan fungsi cleanup untuk menghapus file temp saat program keluar
        atexit.register(self.cleanup_temp_files)

        #state untuk menampilkan bounding box pada frame setelah kode terdeteksi
        self.last_detected_bbox = None     #koordinat bounding box terakhir
        self.last_detected_code = None     #kode yang terakhir terdeteksi
        self.bbox_timestamp = 0            #waktu saat bounding box terakhir diperbarui
        self.bbox_display_duration = 0.8   #durasi tampil bounding box (detik)

        #cooldown setelah scan berhasil: blokir semua trigger scan selama N detik
        #mencegah double scan akibat interval_elapsed atau sisa motion setelah objek diangkat
        self.post_scan_cooldown = 2.0     #detik jeda wajib setelah scan berhasil
        self.last_successful_scan_time = 0 #waktu terakhir scan berhasil menemukan kode

        # ── MOTION-BASED SCAN STATE ──────────────────────────────────────────
        # Variabel untuk logika: scan hanya saat karton BERGERAK MASUK,
        # tidak scan jika karton diam (berapapun lamanya berhenti),
        # reset otomatis saat karton benar-benar keluar dari frame.
        #
        # Cara kerja 3 gerbang:
        #   GERBANG 1 - MOG2 motion: ada gerak? jika tidak → skip
        #   GERBANG 2 - sudah di-scan? jika ya → skip (tidak peduli gerak)
        #   GERBANG 3 - lolos keduanya → jalankan OCR, tandai sudah di-scan
        #   RESET     - setelah NO_MOTION_RESET_THRESHOLD frame kosong berturut-turut
        #               → last_scanned_code = None → siap karton berikutnya
        #
        self._last_scanned_code    = None  # kode karton yang sudah di-scan, None = belum ada
        self._no_motion_count      = 0     # berapa frame berturut-turut tanpa gerak
        # Setelah berapa frame tanpa gerak dianggap karton sudah pergi dan status direset.
        # Pada kamera ~25fps: 25 frame ≈ 1 detik tanpa gerak = karton sudah berlalu.
        # Naikkan jika conveyor sering berhenti sesaat (vibrasi), turunkan jika karton
        # bergerak sangat cepat dan sering terlewat.
        self.NO_MOTION_RESET_THRESHOLD = 25
        # ─────────────────────────────────────────────────────────────────────

        #state untuk motion detection (MOG2 - real-time tanpa delay)
        self.motion_min_area = 500         #luas minimum area gerak agar tidak terpicu noise kecil
        self.motion_detected = False       #flag apakah gerakan terdeteksi di frame terakhir
        #MOG2 background subtractor: history=100 agar adaptif, varThreshold=40 agar tidak terlalu sensitif
        self._fgbg = cv2.createBackgroundSubtractorMOG2(history=100, varThreshold=40, detectShadows=False)
        #bounding box gerakan terakhir untuk ditampilkan di frame (list of (x,y,w,h))
        self.motion_bboxes = []

    #deteksi apakah ada gerakan di frame menggunakan mog2 (real-time, tanpa buffer delay)
    def _detect_motion(self, frame):
        #resize frame kecil untuk perbandingan (lebih cepat, tidak perlu resolusi penuh)
        small = cv2.resize(frame, (320, 240), interpolation=cv2.INTER_AREA)

        #blur ringan untuk mengurangi noise sebelum diproses mog2
        blur = cv2.GaussianBlur(small, (5, 5), 0)

        #terapkan mog2: hasilnya adalah foreground mask (putih = gerak, hitam = background)
        fgmask = self._fgbg.apply(blur)

        #threshold untuk mempertegas area gerak
        _, thresh = cv2.threshold(fgmask, 200, 255, cv2.THRESH_BINARY)

        #cari kontur area yang bergerak
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        #hitung skala balik dari resolusi kecil (320x240) ke resolusi frame asli
        h_orig, w_orig = frame.shape[:2]
        scale_x = w_orig / 320.0
        scale_y = h_orig / 240.0

        detected = False
        bboxes = []
        for contour in contours:
            if cv2.contourArea(contour) < self.motion_min_area:
                continue  #abaikan noise kecil
            detected = True
            #konversi koordinat bounding box ke skala frame asli
            x, y, w, h = cv2.boundingRect(contour)
            bboxes.append((
                int(x * scale_x),
                int(y * scale_y),
                int(w * scale_x),
                int(h * scale_y)
            ))

        self.motion_bboxes = bboxes  #simpan untuk ditampilkan di frame live
        return detected  #true jika ada gerakan signifikan, false jika tidak

    #gambar overlay roi pada frame: area di luar roi digelapkan, roi diberi border orange
    def _draw_roi_overlay(self, frame, roi_name):
        if roi_name == "Full Frame (No ROI)":
            return frame  #tidak perlu overlay

        #ambil koordinat roi dalam format rasio (0.0-1.0) lalu konversi ke piksel
        coords = ROI_COORDS.get(roi_name, (0.0, 0.0, 1.0, 1.0))
        h, w = frame.shape[:2]

        rx1 = int(coords[0] * w)
        ry1 = int(coords[1] * h)
        rx2 = int(coords[2] * w)
        ry2 = int(coords[3] * h)

        #buat overlay gelap untuk area di luar roi
        overlay = frame.copy()
        dark_mask = np.zeros_like(frame, dtype=np.uint8)
        dark_mask[:, :] = (0, 0, 0)   #hitam penuh

        #gelapkan seluruh frame dulu, lalu kembalikan area roi ke asli
        blended = cv2.addWeighted(frame, 0.25, dark_mask, 0.75, 0)
        blended[ry1:ry2, rx1:rx2] = frame[ry1:ry2, rx1:rx2]

        #gambar border orange mencolok di sekeliling roi (tanpa nama label)
        cv2.rectangle(blended, (rx1, ry1), (rx2, ry2), (0, 165, 255), 2)

        return blended

    #crop frame sesuai roi yang dipilih untuk dikirim ke proses ocr
    def _get_roi_crop(self, frame, roi_name):
        if roi_name == "Full Frame (No ROI)":
            return frame, 0, 0  #kembalikan frame utuh + offset (0,0)

        coords = ROI_COORDS.get(roi_name, (0.0, 0.0, 1.0, 1.0))
        h, w = frame.shape[:2]

        rx1 = int(coords[0] * w)
        ry1 = int(coords[1] * h)
        rx2 = int(coords[2] * w)
        ry2 = int(coords[3] * h)

        #pastikan koordinat valid dan area cukup besar
        rx1, rx2 = max(0, rx1), min(w, rx2)
        ry1, ry2 = max(0, ry1), min(h, ry2)

        if rx2 <= rx1 or ry2 <= ry1:
            return frame, 0, 0  #fallback ke full frame jika area tidak valid

        return frame[ry1:ry2, rx1:rx2], rx1, ry1

    #fungsi ini untuk membersihkan file-file sementara yang terdaftar agar tidak menumpuk di disk
    def cleanup_temp_files(self):
        for t_path in self.temp_files_on_exit:
            if os.path.exists(t_path):
                try:
                    os.remove(t_path)
                except:
                    pass

    #loop utama thread untuk membuka kamera dan terus membaca frame hingga dihentikan
    #fungsi ini untuk menjalankan thread deteksi dari awal buka kamera hingga kamera dilepas
    def run(self):
        #coba buka kamera dengan backend DirectShow (Windows) terlebih dahulu
        self.cap = cv2.VideoCapture(self.current_camera_index + cv2.CAP_DSHOW)

        if not self.cap.isOpened():
            #fallback ke backend default jika DirectShow gagal
            self.cap = cv2.VideoCapture(self.current_camera_index)

        #jika kamera tetap tidak bisa dibuka setelah dua percobaan, kirim sinyal error dan hentikan thread
        if not self.cap.isOpened():
            self.camera_status_signal.emit(f"Error: Kamera Index {self.current_camera_index} Gagal Dibuka.", False)
            self.running = False
            return

        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, BUFFER_SIZE)  #kurangi delay dengan buffer kecil
        except:
            pass

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH) #atur lebar dan tinggi frame
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT) #atur lebar dan tinggi frame
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))  #format MJPEG untuk fps lebih tinggi

        self.camera_status_signal.emit("Camera Running", True) #kirim status kamera aktif ke ui

        while self.running:
            self.check_daily_reset() #cek apakah tanggal sudah berganti (penting untuk operasi 3 shift)
            ret, frame = self.cap.read() #baca frame dari kamera

            if not ret:
                break  #keluar dari loop jika frame gagal dibaca

            current_time = time.time()

            #kirim frame ke UI (setiap frame = live)
            self._process_and_send_frame(frame, is_static=False)

            #motion detection (untuk trigger easyocr)
            motion = self._detect_motion(frame)
            #simpan status motion ke atribut dan kirim sinyal ke ui jika tersedia
            self.motion_detected = motion
            if hasattr(self, 'motion_signal') and self.motion_signal:
                self.motion_signal.emit(motion)

            # ── LOGIKA SCAN MOTION-BASED (3 GERBANG) ────────────────────────
            #
            # GERBANG 1: Ada gerak? (dari MOG2 di atas)
            if not motion:
                # Tidak ada gerak → hitung frame kosong berturut-turut
                self._no_motion_count += 1
                # Setelah N frame tanpa gerak → karton sudah pergi → reset status
                if self._no_motion_count >= self.NO_MOTION_RESET_THRESHOLD:
                    if self._last_scanned_code is not None:
                        _logger.debug(
                            "[MOTION] Karton '%s' sudah pergi → reset status scan.",
                            self._last_scanned_code
                        )
                        self._last_scanned_code = None
                    self._no_motion_count = self.NO_MOTION_RESET_THRESHOLD  # cap agar tidak overflow
                # Skip scan — tidak ada gerak
            else:
                # Ada gerak → reset counter diam
                self._no_motion_count = 0

                # GERBANG 2: Sudah di-scan karton ini?
                if self._last_scanned_code is not None:
                    # Karton masih di frame dan sudah di-scan → skip, tidak perlu scan ulang
                    pass
                else:
                    # GERBANG 3: Lolos semua → jalankan OCR
                    if not self.scan_lock.locked():
                        self.last_scan_time = current_time
                        frame_copy = frame.copy()
                        threading.Thread(
                            target=self.scan_frame,
                            args=(frame_copy,),
                            kwargs={'is_static': False, 'original_frame': frame_copy},
                            daemon=True
                        ).start()
            # ─────────────────────────────────────────────────────────────────

        if self.cap:
            self.cap.release()  #lepaskan resource kamera

        self.camera_status_signal.emit("Camera Off", False)

    #fungsi ini untuk menggambar kotak penanda hijau tipis beserta label teks di atas area teks yang terdeteksi
    def _draw_bounding_box(self, frame, bbox, label_text):
        if bbox is None or len(bbox) == 0:
            return frame

        frame_with_box = frame.copy()
        points = np.array(bbox, dtype=np.int32)

        #gambar outline poligon hijau di sekitar area teks (tipis untuk live view)
        cv2.polylines(frame_with_box, [points], isClosed=True, color=(0, 255, 0), thickness=1)

        x_min = int(min([p[0] for p in bbox]))
        y_min = int(min([p[1] for p in bbox]))

        #gambar kotak hijau sebagai background label teks
        text_size = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
        cv2.rectangle(frame_with_box,
                    (x_min, y_min - text_size[1] - 10),
                    (x_min + text_size[0] + 10, y_min),
                    (0, 255, 0), -1)
        #tulis teks label di atas bounding box
        cv2.putText(frame_with_box, label_text,
                    (x_min + 5, y_min - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)

        return frame_with_box

    #fungsi ini untuk menggambar kotak penanda tebal dengan shadow agar terlihat jelas pada foto arsip
    def _draw_bounding_box_save(self, frame, bbox, label_text):
        if bbox is None or len(bbox) == 0:
            return frame

        frame_with_box = frame.copy()
        points = np.array(bbox, dtype=np.int32)

        #outline putih tipis di luar garis hijau agar kontras di latar hitam
        cv2.polylines(frame_with_box, [points], isClosed=True, color=(255, 255, 255), thickness=5)
        #garis hijau tebal di atasnya sebagai warna utama bounding box
        cv2.polylines(frame_with_box, [points], isClosed=True, color=(0, 255, 0), thickness=3)

        x_min = int(min([p[0] for p in bbox]))
        y_min = int(min([p[1] for p in bbox]))

        #background label: shadow hitam luar + isi hijau
        text_size = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)[0]
        rx1 = x_min
        ry1 = y_min - text_size[1] - 12
        rx2 = x_min + text_size[0] + 12
        ry2 = y_min
        cv2.rectangle(frame_with_box, (rx1 - 2, ry1 - 2), (rx2 + 2, ry2 + 2), (0, 0, 0), -1)
        cv2.rectangle(frame_with_box, (rx1, ry1), (rx2, ry2), (0, 255, 0), -1)
        #teks label hitam di atas kotak hijau
        cv2.putText(frame_with_box, label_text,
                    (x_min + 6, y_min - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2, cv2.LINE_AA)

        return frame_with_box

    #fungsi ini untuk memperbarui tampilan ui dengan frame terbaru yang sudah diberi kotak penanda
    def _send_bbox_update(self, frame, bbox, code):
        pass

    #fungsi ini untuk mengolah frame mentah menjadi gambar siap tampil sesuai mode yang aktif lalu dikirim ke ui
    def _process_and_send_frame(self, frame, is_static):
        from PIL import Image
        current_time = time.time()

        if not is_static:
            #center-crop frame asli menjadi persegi
            h, w, _ = frame.shape
            #hitung dimensi dan posisi untuk crop persegi dari tengah frame
            min_dim  = min(h, w)
            cx       = (w - min_dim) // 2  #offset horizontal crop
            cy       = (h - min_dim) // 2  #offset vertikal crop
            frame_cropped = frame[cy:cy + min_dim, cx:cx + min_dim].copy()

            #gambar bounding box motion detection secara live (mog2, tanpa delay)
            for (mx, my, mw, mh) in getattr(self, 'motion_bboxes', []):
                #sesuaikan koordinat motion bbox ke frame yang sudah di-crop
                mx_c = max(mx - cx, 0)
                my_c = max(my - cy, 0)
                mx2_c = min(mx_c + mw, min_dim)
                my2_c = min(my_c + mh, min_dim)
                cv2.rectangle(frame_cropped, (mx_c, my_c), (mx2_c, my2_c), (0, 255, 0), 2)

            #gambar bounding box ocr easyocr (kode jis/din terdeteksi)
            if self.last_detected_bbox is not None and self.last_detected_code is not None:
                #jika durasi tampil bounding box sudah habis, hapus bbox agar tidak ditampilkan lagi
                if current_time - self.bbox_timestamp > self.bbox_display_duration:
                    self.last_detected_bbox = None
                    self.last_detected_code = None
                else:
                    #sesuaikan koordinat bbox agar relatif terhadap frame yang sudah di-crop
                    adjusted_bbox = [
                        [max(p[0] - cx, 0), max(p[1] - cy, 0)]
                        for p in self.last_detected_bbox
                    ]
                    frame_cropped = self._draw_bounding_box(
                        frame_cropped, adjusted_bbox, self.last_detected_code
                    )

            #gambar overlay roi
            frame_cropped = self._draw_roi_overlay(frame_cropped, self.roi_mode)

            #terapkan edge / split mode
            if self.edge_mode:
                frame_cropped = apply_edge_detection(frame_cropped)

            frame_rgb = cv2.cvtColor(frame_cropped, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
            from config import Resampling
            img = img.resize((self.TARGET_WIDTH, self.TARGET_HEIGHT), Resampling)

        else:
            #mode static file: terapkan edge jika aktif, lalu fit gambar ke canvas dengan letterbox
            frame_display = frame.copy()

            if self.edge_mode:
                frame_display = apply_edge_detection(frame_display)

            #gambar overlay roi di atas frame static (hanya untuk tampilan, bukan untuk ocr)
            frame_display = self._draw_roi_overlay(frame_display, self.roi_mode)

            frame_rgb = cv2.cvtColor(frame_display, cv2.COLOR_BGR2RGB)
            original_img = Image.fromarray(frame_rgb)
            original_width, original_height = original_img.size
            #hitung rasio skala agar gambar muat tanpa distorsi (letterbox)
            ratio = min(self.TARGET_WIDTH / original_width, self.TARGET_HEIGHT / original_height)

            new_width = int(original_width * ratio)
            new_height = int(original_height * ratio)

            from config import Resampling

            img_resized = original_img.resize((new_width, new_height), Resampling)
            #buat canvas hitam dan tempel gambar di tengahnya
            img = Image.new('RGB', (self.TARGET_WIDTH, self.TARGET_HEIGHT), 'black')

            #hitung posisi tengah untuk menempel gambar di canvas agar tampil letterbox
            x_offset = (self.TARGET_WIDTH - new_width) // 2
            y_offset = (self.TARGET_HEIGHT - new_height) // 2

            img.paste(img_resized, (x_offset, y_offset))

            #tambahkan label "static file scan" di bagian atas gambar
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(img)

            try:
                font = ImageFont.truetype("arial.ttf", 10)
            except IOError:
                font = ImageFont.load_default()

            text_to_display = "STATIC FILE SCAN"
            bbox = draw.textbbox((0, 0), text_to_display, font=font)
            #hitung posisi horizontal agar teks "static file scan" tampil di tengah gambar
            text_width = bbox[2] - bbox[0]
            x_center = (self.TARGET_WIDTH - text_width) // 2
            y_top = 12

            draw.text((x_center, y_top), text_to_display, fill=(255, 255, 0), font=font)

        self.update_signal.emit(img)  #kirim frame hasil olahan ke ui

    #fungsi ini untuk menyeragamkan penulisan kode din agar bisa dibandingkan secara akurat
    def _normalize_din_code(self, code):
        code = code.strip().upper()
        code_no_space = re.sub(r'\s+', '', code)
        #format terbalik: contoh 490ln3 -> tetap 490ln3
        match = re.match(r'^(\d+[A-Z]?)(LN\d)$', code_no_space)
        if match:
            return code_no_space
        #format lbn: lbn1 -> lbn 1
        match = re.match(r'^(LBN)(\d)$', code_no_space)
        if match:
            return f"{match.group(1)} {match.group(2)}"
        #format ln tanpa kapasitas: ln3 -> ln3
        match = re.match(r'^(LN\d)$', code_no_space)
        if match:
            return match.group(1)
        #format ln dengan kapasitas + suffix + iss: ln4776aiss -> ln4 776a iss
        match = re.match(r'^(LN\d)(\d+)([A-Z])(ISS)$', code_no_space)
        if match:
            return f"{match.group(1)} {match.group(2)}{match.group(3)} {match.group(4)}"
        #format ln dengan kapasitas + suffix: ln3600a -> ln3 600a
        match = re.match(r'^(LN\d)(\d+)([A-Z])$', code_no_space)
        if match:
            return f"{match.group(1)} {match.group(2)}{match.group(3)}"
        #format ln dengan kapasitas saja: ln3600 -> ln3 600
        match = re.match(r'^(LN\d)(\d+)$', code_no_space)
        if match:
            return f"{match.group(1)} {match.group(2)}"
        #fallback: normalisasi spasi dan pastikan iss dipisah dengan spasi
        code_spaced = re.sub(r'\s+', ' ', code).strip()
        code_spaced = re.sub(r'([A-Z0-9])(ISS)$', r'\1 \2', code_spaced)
        return code_spaced

    #fungsi ini untuk memperbaiki kesalahan umum ocr pada teks din seperti huruf yang terbaca sebagai angka
    def _correct_din_structure(self, text):
        #tabel pemetaan karakter yang sering salah dibaca ocr menjadi angka yang benar
        digit_map = {'O':'0','Q':'0','I':'1','L':'1','Z':'2','S':'5','G':'6','B':'8'}  #pemetaan karakter ke angka

        #bersihkan teks dari karakter selain huruf, angka, dan spasi
        text = text.strip().upper()
        text = re.sub(r'[^A-Z0-9\s]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()

        text = re.sub(
            r'LN([OQILZSGB])(?=\s|$|\d)',
            lambda m: 'LN' + digit_map.get(m.group(1), m.group(1)),
            text
        )
        text = re.sub(
            r'LN([OQILZSGB])$',
            lambda m: 'LN' + digit_map.get(m.group(1), m.group(1)),
            text
        )
        #perbaiki penulisan "l n" atau "l b n" yang terpisah oleh spasi menjadi "ln" atau "lbn"
        text = re.sub(r'\bL\s+N\s*([0-6])', r'LN\1', text)
        text = re.sub(r'\bL\s+B\s*N\b', 'LBN', text)
        text = re.sub(r'\s+', ' ', text).strip()

        #deteksi pola terbalik seperti "490ln3" di mana angka kapasitas ada di depan
        m_rev = re.match(r'^([0-9A-Z]{2,5})\s*LN\s*([0-6])\s*$', text)
        if m_rev:
            raw_num = m_rev.group(1)  #ambil angka bagian depan
            corrected_num = ''.join(digit_map.get(c, c) for c in raw_num)  #koreksi huruf menjadi angka
            digits_only = re.sub(r'[A-Z]', '', corrected_num)  #hapus huruf sisa
            final_num = digits_only if len(digits_only) >= 2 else corrected_num  #gunakan digit jika ada cukup
            return f"{final_num}LN{m_rev.group(2)}"

        #deteksi pola terbalik di mana "ln" terbaca sebagai "in", "lh", atau "lm" oleh ocr
        m_rev2 = re.search(r'^([0-9A-Z]{2,5})\s*(?:1N|IN|LH|LM)\s*([0-6])\s*$', text)
        if m_rev2:
            raw_num = m_rev2.group(1)  #ambil angka bagian depan
            corrected_num = ''.join(digit_map.get(c, c) for c in raw_num)  #koreksi huruf menjadi angka
            digits_only = re.sub(r'[A-Z]', '', corrected_num)  #hapus huruf sisa
            final_num = digits_only if len(digits_only) >= 2 else corrected_num  #gunakan digit jika ada cukup
            return f"{final_num}LN{m_rev2.group(2)}"

        m_lna_iss = re.match(r'^(LN[0-6])\s+([0-9A-Z]{2,5})([A-Z])\s+(ISS|I55|IS5|I5S|155|1SS)\s*$', text)
        if m_lna_iss:
            corrected_cap = ''.join(digit_map.get(c, c) for c in m_lna_iss.group(2))  #koreksi kapasitas
            suffix = 'A' if m_lna_iss.group(3) in ['A', '4'] else m_lna_iss.group(3)  #normalisasi suffix
            return f"{m_lna_iss.group(1)} {corrected_cap}{suffix} ISS"

        m_lna = re.match(r'^(LN[0-6])\s+([0-9A-Z]{2,5})([A-Z])\s*$', text)
        if m_lna:
            corrected_cap = ''.join(digit_map.get(c, c) for c in m_lna.group(2))  #koreksi kapasitas
            suffix = 'A' if m_lna.group(3) in ['A', '4'] else m_lna.group(3)  #normalisasi suffix
            return f"{m_lna.group(1)} {corrected_cap}{suffix}"

        text = re.sub(r'^(LBN)(\d)', r'\1 \2', text)
        text = re.sub(r'^(LN[0-6])(\d)', r'\1 \2', text)
        text = re.sub(r'([A-Z0-9])\s*(ISS)$', r'\1 \2', text)
        text = re.sub(r'\s+', ' ', text).strip()

        tokens = text.split()
        if not tokens:
            return text

        #iterasi setiap token dan koreksi karakter sesuai posisinya dalam struktur kode din
        corrected_tokens = []
        for i, token in enumerate(tokens):
            if i == 0:
                #token pertama: perbaiki prefix ln atau lbn
                corrected = ''
                for j, char in enumerate(token):
                    if j == 0:
                        corrected += 'L' if char in ['1', 'I', 'l'] else char
                    elif j == 1:
                        if char == '8':         corrected += 'B'
                        elif char in ['H','M']: corrected += 'N'
                        else:                   corrected += char
                    elif j == 2:
                        if corrected == 'LB':
                            corrected += 'N' if char in ['H','M'] else char
                        else:
                            corrected += digit_map.get(char, char)
                    else:
                        corrected += char
                corrected_tokens.append(corrected)

            elif i == 1:
                #token kedua: perbaiki kapasitas (angka)
                corrected = ''
                for j, char in enumerate(token):
                    is_last = (j == len(token) - 1)
                    if char.isdigit():
                        corrected += char
                    elif is_last and char.isalpha():
                        corrected += 'A' if char == '4' else char
                    else:
                        corrected += digit_map.get(char, char)
                corrected_tokens.append(corrected)

            elif i == 2:
                #token ketiga: perbaiki iss suffix
                norm = token.replace('5','S').replace('1','I').replace('0','O')
                corrected_tokens.append('ISS' if norm in ['ISS','I55','IS5'] else token)

            else:
                corrected_tokens.append(token)  #token lainnya tetap apa adanya

        return ' '.join(corrected_tokens)

    #fungsi ini untuk mencocokkan teks din hasil ocr dengan daftar resmi dan mengembalikan tipe serta skor kemiripan
    def _find_best_din_match(self, detected_text):
        detected_corrected = self._correct_din_structure(detected_text)  #perbaiki kesalahan ocr terlebih dahulu
        detected_clean = detected_corrected.replace(' ', '').upper()  #hapus spasi dan normalisasi ke uppercase

        if len(detected_clean) < 2:
            return None, 0.0

        best_match = None
        best_score = 0.0

        detected_no_iss = re.sub(r'\s*ISS$', '', detected_clean)  #versi tanpa suffix iss

        is_reverse_pattern = bool(re.search(r'LN[0-6]$', detected_clean))  #cek pola terbalik
        is_forward_pattern = bool(re.match(r'^LN[0-6]', detected_clean))  #cek pola normal
        #tentukan ambang batas kemiripan secara adaptif: kode pendek atau pola ln mendapat toleransi lebih tinggi
        if len(detected_clean) <= 4:
            adaptive_threshold = 0.75
        elif is_reverse_pattern or is_forward_pattern:
            adaptive_threshold = 0.70
        else:
            adaptive_threshold = 0.82

        for din_type in DIN_TYPES[1:]:
            target_clean = din_type.replace(' ', '').upper()  #normalisasi target dari daftar resmi

            if detected_clean == target_clean:
                return din_type, 1.0  #kecocokan sempurna

            ratio = SequenceMatcher(None, detected_clean, target_clean).ratio()  #hitung kemiripan
            if ratio >= adaptive_threshold and ratio > best_score:
                best_score = ratio
                best_match = din_type

                #coba bandingkan ulang tanpa suffix iss jika skor awal di bawah 0.88
            if ratio < 0.88:
                target_no_iss = re.sub(r'ISS$', '', target_clean)  #versi target tanpa iss
                if detected_no_iss != detected_clean or target_no_iss != target_clean:
                    ratio_no_iss = SequenceMatcher(None, detected_no_iss, target_no_iss).ratio()  #hitung kemiripan tanpa iss
                    if ratio_no_iss >= 0.88 and ratio_no_iss > best_score:
                        if 'ISS' in detected_clean and 'ISS' not in din_type:
                            iss_candidate = din_type + ' ISS'  #tambahkan iss suffix ke kandidat
                            if iss_candidate in DIN_TYPES:
                                best_score = ratio_no_iss
                                best_match = iss_candidate
                        elif 'ISS' not in din_type:
                            best_score = ratio_no_iss
                            best_match = din_type

        if not best_match and re.match(r'^(\d+)LN([0-6])$', detected_clean):
            #coba kecocokan persis untuk pola terbalik jika belum ada match
            for din_type in DIN_TYPES[1:]:
                if din_type.replace(' ', '').upper() == detected_clean:
                    return din_type, 1.0

        return best_match, best_score

    #koreksi struktur teks jis hasil ocr
    #fungsi ini untuk memperbaiki kesalahan umum ocr pada teks jis seperti angka yang terbaca sebagai huruf
    def _correct_jis_structure(self, text):
        text = text.strip().upper().replace(' ', '')

        #tabel konversi dua arah: angka ke huruf (untuk karakter tengah jis) dan huruf ke angka (untuk kapasitas/ukuran)
        digit_to_letter = {
            '0': 'D', '1': 'I', '2': 'Z', '3': 'B',
            '4': 'A', '5': 'S', '6': 'G', '8': 'B',
        }  #pemetaan angka ke huruf untuk karakter tengah

        letter_to_digit = {
            'O': '0', 'Q': '0',
            'I': '1', 'L': '1',
            'Z': '2',
            'S': '5',
            'G': '6',
            'B': '8',
        }  #pemetaan huruf ke angka untuk kapasitas/ukuran

        #normalisasi berbagai variasi penulisan suffix "(s)" yang salah terbaca oleh ocr
        text = re.sub(r'\(5\)', r'(S)', text)
        text = re.sub(r'5\)', r'(S)', text)
        text = re.sub(r'\([S5](?!\))', r'(S)', text)

        #pisahkan suffix "(s)" dan terminal "l"/"r" dari teks utama sebelum analisis struktur
        option = ''
        main_text = text
        if main_text.endswith('(S)'):
            option = '(S)'
            main_text = main_text[:-3]

        terminal = ''
        if main_text and main_text[-1] in ['L', 'R']:
            terminal = main_text[-1]
            main_text = main_text[:-1]

        #cari posisi karakter huruf tengah jis (a-h) di posisi ke-2 atau ke-3
        if len(main_text) >= 5:
            for mid_pos in [2, 3]:
                if mid_pos < len(main_text):
                    potential_mid = main_text[mid_pos]

                    if potential_mid in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']:
                        raw_cap = main_text[:mid_pos]  #bagian kapasitas sebelum karakter tengah
                        mid_char = potential_mid
                        raw_size = main_text[mid_pos+1:]  #bagian ukuran setelah karakter tengah

                        cap_corrected = ''.join(letter_to_digit.get(c, c) for c in raw_cap)  #koreksi kapasitas
                        size_corrected = ''.join(letter_to_digit.get(c, c) for c in raw_size)  #koreksi ukuran

                        if cap_corrected.isdigit() and size_corrected.isdigit():
                            return f'{cap_corrected}{mid_char}{size_corrected}{terminal}{option}'
                        break

                    elif potential_mid.isdigit():
                        corrected_letter = digit_to_letter.get(potential_mid, 'D')  #konversi digit ke huruf
                        if corrected_letter in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']:
                            raw_cap = main_text[:mid_pos]  #bagian kapasitas
                            mid_char = corrected_letter
                            raw_size = main_text[mid_pos+1:]  #bagian ukuran

                            cap_corrected = ''.join(letter_to_digit.get(c, c) for c in raw_cap)  #koreksi kapasitas
                            size_corrected = ''.join(letter_to_digit.get(c, c) for c in raw_size)  #koreksi ukuran

                            if cap_corrected.isdigit() and size_corrected.isdigit():
                                return f'{cap_corrected}{mid_char}{size_corrected}{terminal}{option}'
                            break

        pattern = r'^(\d{2,3})([A-Z0-9])(\d{2,3})([LR])?(\(S\))?$'
        match = re.match(pattern, text)
        if match:
            capacity = match.group(1)
            middle_char = match.group(2)
            size = match.group(3)
            terminal = match.group(4) or ''
            option = match.group(5) or ''

            if middle_char.isdigit():
                corrected_letter = digit_to_letter.get(middle_char, 'D')  #konversi digit ke huruf
                if corrected_letter in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']:
                    middle_char = corrected_letter

            corrected = f"{capacity}{middle_char}{size}{terminal}{option}"
            return corrected

        return text

    #cari kecocokan terbaik kode jis dari daftar jis_types menggunakan kemiripan string
    #fungsi ini untuk mencocokkan teks jis hasil ocr dengan daftar resmi dan mengembalikan tipe serta skor kemiripan
    def _find_best_jis_match(self, detected_text):
        detected_corrected = self._correct_jis_structure(detected_text)  #perbaiki kesalahan ocr terlebih dahulu
        detected_clean = detected_corrected.replace(' ', '').upper()  #hapus spasi dan normalisasi ke uppercase

        #cek kecocokan persis dulu sebelum masuk ke fuzzy matching untuk efisiensi
        for jis_type in JIS_TYPES[1:]:
            if detected_clean == jis_type.replace(' ', '').upper():
                return jis_type, 1.0  #kecocokan sempurna

        best_match = None
        best_score = 0.0

        for jis_type in JIS_TYPES[1:]:
            target_clean = jis_type.replace(' ', '').upper()  #normalisasi target dari daftar resmi
            ratio = SequenceMatcher(None, detected_clean, target_clean).ratio()  #hitung kemiripan

            if ratio > 0.85 and ratio > best_score:
                best_score = ratio
                best_match = jis_type

        #jika belum ditemukan kecocokan yang baik, coba cocokkan lagi dengan mengabaikan suffix "(s)"
        if not best_match or best_score < 0.90:
            detected_without_s = detected_clean.replace('(S)', '')  #versi tanpa suffix (s)

            for jis_type in JIS_TYPES[1:]:
                target_without_s = jis_type.replace(' ', '').replace('(S)', '').upper()  #normalisasi target tanpa (s)
                ratio = SequenceMatcher(None, detected_without_s, target_without_s).ratio()  #hitung kemiripan tanpa (s)

                if ratio > 0.90:
                    if '(S)' in detected_clean:
                        base_code = jis_type.replace('(S)', '')  #ambil base code dari target
                        candidate_with_s = base_code + '(S)'  #tambahkan (s) suffix

                        if candidate_with_s in JIS_TYPES:
                            best_match = candidate_with_s
                            best_score = ratio
                            break
                    else:
                        if '(S)' not in jis_type and ratio > best_score:
                            best_match = jis_type
                            best_score = ratio

        return best_match, best_score

    #fungsi inti yaitu menjalankan ocr pada frame, mencocokkan kode, dan menyimpan hasilnya
    #fungsi ini untuk memproses satu frame gambar secara penuh mulai dari ocr hingga penyimpanan ke database
    def scan_frame(self, frame, is_static=False, original_frame=None):
        #ambil snapshot preset dan label saat ini agar konsisten selama proses scan berjalan
        current_preset = self.preset
        current_target_label = self.target_label

        best_match = None
        best_match_bbox = None

        frame_to_save = original_frame if original_frame is not None else frame

        if not is_static:
            if not self.scan_lock.acquire(blocking=False):
                return  #jika sudah ada scan berjalan, hentikan

            if self.scan_start_signal:
                self.scan_start_signal.emit()  #kirim sinyal scan dimulai ke ui

            #crop frame menjadi persegi dari tengah agar konsisten dengan tampilan ui
            h_orig, w_orig, _ = frame.shape
            min_dim_orig = min(h_orig, w_orig)
            start_x_orig = (w_orig - min_dim_orig) // 2
            start_y_orig = (h_orig - min_dim_orig) // 2
            frame = frame[start_y_orig:start_y_orig + min_dim_orig, start_x_orig:start_x_orig + min_dim_orig]

            roi_name = self.roi_mode
            frame, roi_offset_x, roi_offset_y = self._get_roi_crop(frame, roi_name)  #crop area roi jika aktif

            #tambahkan offset crop persegi ke offset roi agar koordinat bbox tetap akurat terhadap frame asli
            roi_offset_x += start_x_orig
            roi_offset_y += start_y_orig

            if self.edge_mode:
                frame = apply_edge_detection(frame)  #terapkan deteksi tepi jika aktif


        try:
            if is_static:
                roi_name = self.roi_mode
                frame, roi_offset_x, roi_offset_y = self._get_roi_crop(frame, roi_name)  #crop area roi untuk file statis

            #downscale frame ke maksimal lebar 320px untuk mempercepat proses ocr di cpu
            h, w = frame.shape[:2]
            scale_factor = 1.0
            if w > 320:
                scale_factor = 320 / w
                new_w, new_h = 320, int(h * scale_factor)
                frame_small = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
            else:
                frame_small = frame

            #konversi frame ke grayscale untuk tahap pertama ocr
            gray = cv2.cvtColor(frame_small, cv2.COLOR_BGR2GRAY)

            all_results = []
            all_results_with_bbox = []

            #tentukan daftar karakter yang diperbolehkan ocr sesuai preset aktif untuk mengurangi kesalahan baca
            if current_preset == "JIS":
                allowlist_chars = ALLOWLIST_JIS
            else:
                allowlist_chars = ALLOWLIST_DIN

            #tahap 1: grayscale biasa — cepat, dijalankan selalu
            #tahap 2: clahe — hanya dijalankan jika tahap 1 tidak menghasilkan teks apapun
            processing_stages = [('Grayscale', gray)]

            for stage_name, processed_frame in processing_stages:
                try:
                    min_sz = 8
                    w_ths = 0.5 if current_preset == "DIN" else 0.7  #threshold lebar berbeda per tipe

                    results = self.reader.readtext(
                        processed_frame,
                        detail=1,
                        paragraph=False,
                        min_size=min_sz,
                        width_ths=w_ths,
                        allowlist=allowlist_chars,
                        decoder='greedy',
                        beamWidth=1,
                    )  #jalankan easyocr readtext

                    stage_scale = scale_factor

                    #skalakan koordinat bbox kembali ke resolusi frame asli dan tambahkan offset roi
                    for result in results:
                        bbox, text, confidence = result
                        scaled_bbox = [[int(x / stage_scale) + roi_offset_x, int(y / stage_scale) + roi_offset_y] for x, y in bbox]  #skalakan dan offset bbox
                        all_results.append(text)
                        all_results_with_bbox.append({'text': text, 'bbox': scaled_bbox, 'confidence': confidence})

                except Exception as e:
                    _logger.debug(f"OCR error on {stage_name}: {e}")
                    continue

            #jika tahap grayscale tidak menghasilkan teks sama sekali, coba sekali lagi dengan clahe
            if not all_results_with_bbox:
                try:
                    clahe_frame = self._clahe.apply(gray)  #terapkan clahe untuk kontras lebih baik
                    results = self.reader.readtext(
                        clahe_frame,
                        detail=1,
                        paragraph=False,
                        min_size=8,
                        width_ths=0.5 if current_preset == "DIN" else 0.7,
                        allowlist=allowlist_chars,
                        decoder='greedy',
                        beamWidth=1,
                    )  #jalankan easyocr ulang dengan clahe
                    for result in results:
                        bbox, text, confidence = result
                        scaled_bbox = [[int(x / scale_factor) + roi_offset_x, int(y / scale_factor) + roi_offset_y] for x, y in bbox]  #skalakan dan offset bbox
                        all_results.append(text)
                        all_results_with_bbox.append({'text': text, 'bbox': scaled_bbox, 'confidence': confidence})
                except Exception as e:
                    _logger.debug(f"OCR error on CLAHE fallback: {e}")

            #untuk preset din, gabungkan teks-teks yang berdekatan secara horizontal menjadi satu token
            if current_preset == "DIN" and all_results_with_bbox:
                def _group_adjacent(results_bbox, max_h_gap=60, max_v_diff=20):
                    #fungsi helper untuk mengelompokkan teks yang berdekatan
                    if not results_bbox: return results_bbox
                    def bi(bbox):
                        #hitung bounding box rectangle dari polygon
                        xs=[p[0] for p in bbox]; ys=[p[1] for p in bbox]
                        return min(xs),min(ys),max(xs),max(ys)
                    items = sorted(results_bbox, key=lambda r: bi(r['bbox'])[0])  #sort by x position
                    used = [False]*len(items); grouped = []
                    for i, item in enumerate(items):
                        if used[i]: continue
                        x1i,y1i,x2i,y2i = bi(item['bbox'])
                        texts=[item['text']]; confs=[item['confidence']]; used[i]=True
                        for j, other in enumerate(items):
                            if used[j] or i==j: continue
                            x1j,y1j,x2j,y2j = bi(other['bbox'])
                            cy_i=(y1i+y2i)/2; cy_j=(y1j+y2j)/2  #hitung center y
                            if abs(cy_i-cy_j)>max_v_diff: continue  #abaikan jika vertikal jauh
                            if 0<=x1j-x2i<=max_h_gap:
                                #jika items berdekatan horizontal, gabungkan
                                texts.append(other['text']); confs.append(other['confidence'])
                                used[j]=True; x2i=x2j
                        if len(texts)>1:
                            #jika ada penggabungan, buat token baru
                            grouped.append({'text':' '.join(texts),'bbox':item['bbox'],
                                            'confidence':sum(confs)/len(confs)})
                        else:
                            grouped.append(item)
                    return grouped
                grouped_results = _group_adjacent(all_results_with_bbox)
                for gr in grouped_results:
                    if ' ' in gr['text'] and gr['text'] not in all_results:
                        all_results.append(gr['text'])
                        all_results_with_bbox.append(gr)

            #kirim semua hasil teks ocr yang unik ke ui agar bisa ditampilkan di panel debug
            if self.all_text_signal:
                unique_results = list(set(all_results))
                self.all_text_signal.emit(unique_results)  #kirim hasil ke ui

            best_match_text = None
            best_match_score = 0.0

            if current_preset == "DIN":
                #cari kecocokan terbaik untuk preset din
                for result_data in all_results_with_bbox:
                    text = result_data['text']
                    bbox = result_data['bbox']

                    #lewati teks yang terlalu pendek karena tidak mungkin merupakan kode din yang valid
                    if len(text.replace(' ', '')) < 3:
                        continue

                    matched_type, score = self._find_best_din_match(text)  #cari match terbaik

                    if matched_type and score > best_match_score:
                        best_match_score = score
                        best_match_text = matched_type
                        best_match_bbox = bbox

                if best_match_text and best_match_score > 0.85:
                    best_match = best_match_text

            else:
                #cari kecocokan terbaik untuk preset jis
                for result_data in all_results_with_bbox:
                    text = result_data['text']
                    bbox = result_data['bbox']

                    #lewati teks yang terlalu pendek karena tidak mungkin merupakan kode jis yang valid
                    if len(text.replace(' ', '').replace('(S)', '')) < 5:
                        continue

                    matched_type, score = self._find_best_jis_match(text)  #cari match terbaik

                    if matched_type and score > best_match_score:
                        best_match_score = score
                        best_match_text = matched_type
                        best_match_bbox = bbox

                if best_match_text and best_match_score > 0.85:
                    best_match = best_match_text

            if best_match:
                detected_code = best_match.strip()

                self.last_detected_bbox = best_match_bbox
                self.last_detected_code = detected_code
                self.bbox_timestamp = time.time()  #simpan waktu deteksi untuk timeout bbox

                #normalisasi format kode akhir sesuai tipenya sebelum disimpan ke database
                if current_preset == "DIN":
                    detected_code = self._normalize_din_code(detected_code)  #normalisasi format din
                else:
                    detected_code = detected_code.replace(' ', '')  #hapus spasi untuk jis

                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                detected_type = self._detect_code_type(detected_code)  #deteksi tipe kode

                if detected_type is None:
                    self.code_detected_signal.emit("Format kode tidak valid")
                    if not is_static:
                        self.scan_lock.release()
                    return
                if detected_type != current_preset:
                    #validasi tipe kode sesuai preset aktif
                    msg = "Pastikan foto anda adalah Type JIS" if current_preset == "JIS" else "Pastikan foto anda adalah Type DIN"
                    self.code_detected_signal.emit(msg)
                    if not is_static:
                        self.scan_lock.release()
                    return

                #bandingkan kode terdeteksi dengan target untuk menentukan status ok atau not ok
                if current_preset == "DIN":
                    target_for_compare = getattr(self, 'target_label_compare', current_target_label)
                    target_normalized = self._normalize_din_code(target_for_compare)  #normalisasi target
                    detected_normalized = self._normalize_din_code(detected_code)  #normalisasi deteksi
                    status = "OK" if detected_normalized.upper() == target_normalized.upper() else "Not OK"
                else:
                    target_for_compare = getattr(self, 'target_label_compare', current_target_label)
                    status = "OK" if detected_code == target_for_compare else "Not OK"

                target_session = current_target_label if current_target_label else detected_code

                if not is_static:
                    #cek duplikat via dictionary — o(1) instant
                    last_scan_time = self.recent_scans.get(detected_code, 0)
                    if time.time() - last_scan_time < 3:
                        return  #kode sama sudah di-scan dalam 3 detik terakhir, skip

                #struktur penyimpanan gambar: images/tanggal/label/karton_xxx.jpg
                safe_label = re.sub(r'[\\/:*?"<>|]', '_', target_session) if target_session else 'unknown'  #sanitasi nama label
                #buat subfolder untuk label ini jika belum ada dan simpan pathnya ke cache
                if safe_label not in self.label_folders:
                    label_path = os.path.join(self.today_date_folder, safe_label)
                    os.makedirs(label_path, exist_ok=True)
                    self.label_folders[safe_label] = label_path  #cache path subfolder
                #buat nama file gambar unik menggunakan timestamp agar tidak tertimpa
                img_filename = f"karton_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                img_path = os.path.join(self.label_folders[safe_label], img_filename)

                if best_match_bbox is not None:
                    frame_with_box = self._draw_bounding_box_save(frame_to_save, best_match_bbox, detected_code)  #gambar bbox di gambar
                    frame_binary = convert_frame_to_binary(frame_with_box)  #konversi ke binary
                else:
                    frame_binary = convert_frame_to_binary(frame_to_save)

                #resize frame ke 640x480 agar ukuran file lebih kecil
                #kualitas jpeg diset 60 - cukup jelas untuk keperluan arsip dan review
                #bounding box tetap tergambar karena resize dilakukan setelah frame_binary dibuat
                frame_compressed = cv2.resize(frame_binary, (640, 480), interpolation=cv2.INTER_AREA)
                cv2.imwrite(img_path, frame_compressed, [cv2.IMWRITE_JPEG_QUALITY, 60])  #simpan ke file

                new_id = insert_detection(timestamp, detected_code, current_preset, img_path, status, target_session)  #insert ke database

                #tambahkan record baru ke list lokal agar ui bisa langsung menampilkan hasilnya
                if new_id:
                    record = {
                        "ID": new_id,
                        "Time": timestamp,
                        "Code": detected_code,
                        "Type": current_preset,
                        "ImagePath": img_path,
                        "Status": status,
                        "TargetSession": target_session
                    }

                    self.detected_codes.append(record)  #tambah ke list lokal

                #catat waktu scan terakhir kode ini di dictionary
                self.recent_scans[detected_code] = time.time()
                #catat waktu scan berhasil untuk cooldown trigger (mencegah double scan)
                if not is_static:
                    self.last_successful_scan_time = time.time()
                    #tandai karton ini sudah di-scan → gerbang 2 akan memblokir scan ulang
                    #selama karton masih di frame. reset hanya terjadi saat karton keluar.
                    self._last_scanned_code = detected_code

                #kirim sinyal ke ui bahwa kode berhasil terdeteksi
                self.code_detected_signal.emit(detected_code)

                if not is_static:
                    threading.Thread(target=self._send_bbox_update,
                                    args=(frame_to_save.copy(), best_match_bbox, detected_code),
                                    daemon=True).start()

            #jika tidak ada kode yang cocok, reset bbox dan kirim sinyal gagal jika mode static
            else:
                self.last_detected_bbox = None
                self.last_detected_code = None

                if is_static:
                    self.code_detected_signal.emit("FAILED")

        except Exception as e:
            _logger.debug(f"OCR/Regex error: {e}")
            if is_static:
                self.code_detected_signal.emit(f"ERROR: {e}")

        #pastikan scan_lock selalu dilepas setelah proses selesai meskipun terjadi error
        finally:
            if not is_static:
                self.scan_lock.release()

    #fungsi ini untuk memulai proses deteksi dengan menjalankan thread jika belum berjalan
    def start_detection(self):
        if self.running:
            return  #jika sudah berjalan, abaikan
        self.running = True
        self.start()  #mulai thread deteksi

    #fungsi ini untuk menghentikan proses deteksi dan melepaskan resource kamera
    def stop_detection(self):
        self.running = False  #flag stop loop thread

        self.last_detected_bbox = None
        self.last_detected_code = None
        self._last_scanned_code = None
        self._no_motion_count   = 0

        if self.cap:
            self.cap.release()  #lepaskan resource kamera

    #fungsi ini untuk mengatur opsi kamera seperti preset, edge mode, dan interval scan
    def set_camera_options(self, preset, edge_mode, scan_interval):
        self.preset = preset  #set tipe kode (jis/din)
        self.edge_mode = edge_mode  #set mode deteksi tepi
        self.scan_interval = scan_interval  #set interval scan

    #fungsi ini untuk mengatur label target yang akan digunakan sebagai pembanding hasil deteksi ocr
    def set_target_label(self, label):
        self.target_label = label

        #ekstrak bagian kode jis/din dari label lengkap untuk digunakan saat perbandingan hasil ocr
        match = re.search(r'(\d{2,3}[A-H]\d{2,3}[LR]?(?:\(S\))?)', label)
        self.target_label_compare = match.group(1) if match else label  #simpan bagian kode untuk komparasi

    #fungsi ini untuk mengecek apakah tanggal sudah berganti dan mereset data deteksi jika hari baru
    def check_daily_reset(self):
        now = datetime.now()
        new_date = now.date()

        if new_date > self.current_date:
            self.current_date = new_date  #update tanggal saat ini
            #kosongkan list lama lalu muat ulang data dari database untuk tanggal baru
            self.detected_codes = []
            self.detected_codes = load_existing_data(self.current_date, self.preset)  #muat data dari database untuk hari baru
            #reset dictionary duplicate check saat ganti hari
            self.recent_scans = {}
            #buat subfolder tanggal baru dan reset cache label saat ganti hari
            self.today_date_folder = os.path.join(IMAGE_DIR, self.current_date.strftime("%Y-%m-%d"))
            os.makedirs(self.today_date_folder, exist_ok=True)
            self.label_folders = {}  #reset cache label — hari baru mulai dari kosong
            self.data_reset_signal.emit()  #kirim sinyal reset ke ui

            return True

        return False

    #fungsi ini untuk memproses file gambar statis dan menjalankan ocr padanya tanpa menggunakan kamera
    def scan_file(self, filepath):
        if self.running:
            return "STOP_LIVE"  #jika live camera masih berjalan, stop dulu

        try:
            #baca file gambar; kembalikan error jika file tidak valid atau tidak bisa dibaca
            frame = cv2.imread(filepath)
            if frame is None or frame.size == 0:
                return "LOAD_ERROR"

            self._process_and_send_frame(frame, is_static=True)  #proses frame untuk ditampilkan

            #jalankan scan_frame di thread daemon agar tidak memblokir respons fungsi ini
            threading.Thread(target=self.scan_frame,
                            args=(frame.copy(),),
                            kwargs={'is_static': True, 'original_frame': frame.copy()},
                            daemon=True).start()

            return "SCANNING"

        except Exception as e:
            _logger.debug(f"File scan error: {e}")
            return f"PROCESS_ERROR: {e}"

    #fungsi ini untuk mendeteksi apakah kode yang ditemukan bertipe jis atau din berdasarkan polanya
    def _detect_code_type(self, code):
        code_normalized = code.replace(' ', '').upper()

        #check pola jis: 2-3 digit, 1 huruf (a-h), 2-3 digit, optional (l/r), optional ((s))
        if re.match(r"^\d{2,3}[A-H]\d{2,3}[LR]?(?:\(S\))?$", code_normalized):
            return "JIS"

        #check kecocokan persis dengan daftar din types
        for din_type in DIN_TYPES[1:]:
            if code_normalized == din_type.replace(' ', '').upper():
                return "DIN"

        #daftar pola regex tambahan untuk mengenali berbagai format kode din yang valid
        din_patterns = [
            r'^LBN\d$',
            r'^LN[0-6]$',
            r'^LN[0-6]\d{2,5}[A-Z]?$',
            r'^LN[0-6]\d{2,5}[A-Z]ISS$',
            r'^\d{2,5}LN[0-6]$',
        ]
        for pattern in din_patterns:
            if re.match(pattern, code_normalized):
                return "DIN"

        return None

    #fungsi ini untuk memvalidasi apakah tipe kode yang terdeteksi sesuai dengan preset yang sedang aktif
    def _validate_preset_match(self, detected_code, detected_type):
        if detected_type is None:
            return False, "Format kode tidak valid"

        if detected_type != self.preset:
            if self.preset == "JIS":
                return False, "Pastikan foto anda adalah Type JIS"
            else:
                return False, "Pastikan foto anda adalah Type DIN"
        return True, ""

    #fungsi ini untuk menghapus record deteksi berdasarkan id dari database dan memperbarui data lokal
    def delete_codes(self, record_ids):
        from database import delete_codes

        if delete_codes(record_ids, self.preset):
            #hapus record dari list lokal agar ui langsung sinkron tanpa perlu reload dari database
            self.detected_codes = [rec for rec in self.detected_codes if rec['ID'] not in record_ids]
            return True

        return False