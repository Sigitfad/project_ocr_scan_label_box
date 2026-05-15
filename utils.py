import os  #untuk operasi file dan direktori
import re  #untuk operasi regex dalam koreksi ocr
import numpy as np  #untuk operasi array/matriks (opencv)
import cv2  #opencv untuk pemrosesan gambar dan akses kamera
from datetime import datetime  #untuk format timestamp saat menyimpan data
from config import Resampling  #metode resampling gambar dari config (kompatibilitas PIL)

#sembunyikan log debug opencv yang tidak diperlukan agar output terminal lebih bersih
os.environ['OPENCV_LOG_LEVEL'] = 'ERROR'  #set level log opencv ke ERROR saja, menghilangkan debug dan warning
os.environ['OPENCV_VIDEOIO_DEBUG'] = '0'  #disable debug output untuk video I/O (kamera)
cv2.setLogLevel(0)  #set log level cv2 ke level terendah untuk meminimalkan output

#untuk mengubah frame BGR menjadi gambar tepi (edge detection) berwarna hitam-putih
def apply_edge_detection(frame):  #fungsi untuk deteksi tepi pada frame gambar
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)  #konversi frame dari BGR ke grayscale (skala abu-abu)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)  #apply gaussian blur dengan kernel 3x3 untuk mengurangi noise
    edges = cv2.Canny(blurred, 30, 100)  #deteksi tepi menggunakan algoritma Canny dengan threshold 30-100
    kernel = np.ones((2, 2), np.uint8)  #buat kernel 2x2 berisi nilai 1 untuk operasi morphological
    edges_dilated = cv2.dilate(edges, kernel, iterations=1)  #perlebar garis tepi menggunakan dilasi dengan 1 iterasi
    #buat frame BGR kosong (hitam) lalu warnai piksel tepi menjadi putih
    edges_bgr = np.zeros((edges_dilated.shape[0], edges_dilated.shape[1], 3), dtype=np.uint8)  #buat array kosong dengan shape sama seperti edges, tipe uint8, berisi 0 (hitam)
    edges_bgr[edges_dilated > 0] = [255, 255, 255]  #set piksel yang merupakan tepi (nilai > 0) menjadi putih (255,255,255)
    return edges_bgr  #kembalikan frame BGR hasil edge detection berwarna hitam-putih

#untuk koreksi teks hasil ocr yang salah baca untuk kode baterai standar JIS
#format JIS: [kapasitas][grup A-H][tinggi][L/R?][(S)?]
def fix_common_ocr_errors_jis(text):  #fungsi untuk koreksi kesalahan baca OCR pada format kode JIS
    text = text.strip().upper()  #hapus spasi awal/akhir dan ubah ke uppercase
    text = re.sub(r'[^A-Z0-9()]', '', text)  #hapus semua karakter selain huruf A-Z, angka 0-9, dan tanda kurung

    #huruf yang sering salah baca sebagai angka di posisi kapasitas/tinggi
    char_to_digit = {  #mapping huruf yang mirip dengan angka untuk koreksi
        "O": "0", "Q": "0", "D": "0", "U": "0", "C": "0",  #huruf yang mirip angka 0
        "I": "1", "L": "1", "J": "1",  #huruf yang mirip angka 1
        "Z": "2", "E": "3", "A": "4", "H": "4",  #huruf yang mirip angka 2,3,4
        "S": "5", "G": "6", "T": "7", "Y": "7",  #huruf yang mirip angka 5,6,7
        "B": "8", "P": "9", "R": "9"  #huruf yang mirip angka 8,9
    }

    #peta konversi yaitu angka yang sering salah baca sebagai huruf di posisi grup (tengah kode)
    digit_to_char = {  #mapping angka yang mirip dengan huruf untuk koreksi grup karakter
        "0": "D", "1": "L", "2": "Z", "3": "B", "4": "A", "5": "S",  #angka yang sering salah baca sebagai huruf
        "6": "G", "7": "T", "8": "B", "9": "R", "D": "G"  #pemetaan tambahan untuk normalisasi
    }

    #coba cocokkan pola JIS lengkap: kapasitas + grup + tinggi + terminal? + (S)?
    match = re.search(r'(\d+|[A-Z]+)(\d+|[A-Z])(\d+|[A-Z]+)([L|R|1|0|4|D|I]?)(\(S\)|5\)|S)?$', text)  #regex untuk matching pola JIS standard

    if match:  #jika pola ditemukan
        capacity = match.group(1)  #group 1: bagian kapasitas (angka atau huruf)
        type_char = match.group(2)  #group 2: karakter grup standar (satu karakter)
        size = match.group(3)  #group 3: bagian tinggi/size (angka atau huruf)
        terminal = match.group(4)  #group 4: terminal opsional (L/R/1/0/4/D/I atau kosong)
        option = match.group(5)  #group 5: suffix opsional ((S) atau 5) atau kosong)

        #koreksi kapasitas: semua huruf di bagian ini harus jadi angka
        new_capacity = "".join([char_to_digit.get(c, c) for c in capacity])  #konversi setiap karakter huruf menjadi angka menggunakan char_to_digit mapping

        #koreksi karakter grup: jika angka, konversi ke huruf yang sesuai
        if type_char.isdigit():  #cek apakah group character adalah angka
            new_type = digit_to_char.get(type_char, type_char)  #konversi angka menjadi huruf sesuai mapping
        else:  #jika sudah huruf
            new_type = type_char  #gunakan apa adanya

        #normalisasi karakter grup ke huruf yang valid (A-H)
        if new_type in ['O', 'Q', 'G', '0', 'U', 'C']: new_type = 'D'  #huruf-huruf mirip konversi jadi D
        if new_type in ['8', '3']: new_type = 'B'  #angka mirip konversi jadi B
        if new_type in ['4']: new_type = 'A'  #angka 4 yang mirip konversi jadi A
 
        #jika ada L/R di akhir bagian tinggi dan terminal belum ada, pindahkan ke terminal
        size_digit_only = ''  #variable untuk menyimpan size yang sudah dikoreksi (hanya angka)
        size_extra_terminal = ''  #variable untuk menyimpan terminal yang ditemukan di bagian size
        for idx_c, c in enumerate(size):  #iterasi setiap karakter dalam bagian size
            if c.isdigit():  #jika karakter adalah angka
                size_digit_only += c  #tambahkan langsung
            elif c in char_to_digit:  #jika karakter adalah huruf yang perlu dikonversi ke angka
                size_digit_only += char_to_digit[c]  #konversi dan tambahkan
            elif c in ['L', 'R'] and idx_c >= len(size) - 1 and not terminal:  #jika L/R di akhir size dan terminal belum ada
                size_extra_terminal = c  #simpan sebagai terminal yang salah posisi
            else:  #karakter lain
                size_digit_only += c  #tambahkan apa adanya
        new_size = size_digit_only  #assign hasil koreksi ke new_size
        if size_extra_terminal and not terminal:  #jika ada terminal yang ditemukan di size dan terminal masih kosong
            terminal = size_extra_terminal  #pindahkan terminal dari size ke terminal variable

        #koreksi terminal: karakter yang mirip L/R dikembalikan ke L atau R
        if terminal:  #jika ada terminal
            if terminal in ['1', 'I', 'J', '4']:  #jika terminal mirip dengan L
                terminal = 'L'  #normalisasi ke L
            elif terminal in ['0', 'Q', 'D', 'O']:  #jika terminal mirip dengan R
                terminal = 'R'  #normalisasi ke R

        #normalisasi suffix (S): semua variasi diubah ke bentuk standar (S)
        if option:  #jika ada option/suffix
            option = '(S)'  #normalisasi semua variasi (5), (5), S) menjadi (S)

        text_fixed = f"{new_capacity}{new_type}{new_size}{terminal}{option if option else ''}"  #gabung semua bagian yang sudah dikoreksi
        return text_fixed.strip().upper()  #kembalikan hasil dengan spasi dihapus dan converted ke uppercase

    #fallback: ganti semua karakter yang mirip angka jika pola utama tidak cocok
    for char, digit in char_to_digit.items():  #iterasi mapping huruf ke angka
        text = text.replace(char, digit)  #replace semua huruf mirip dengan angka yang sesuai

    text = text.replace('5)', '(S)').replace('(5)', '(S)')  #normalisasi suffix (S) dari berbagai variasi
    return text.strip().upper()  #kembalikan hasil setelah fallback processing

#untuk koreksi teks hasil ocr yang salah baca untuk kode baterai standar DIN
#format DIN: LBN/LN + angka + kapasitas atau format terbalik
def fix_common_ocr_errors_din(text):  #fungsi untuk koreksi kesalahan baca OCR pada format kode DIN
    text = text.strip().upper()  #hapus spasi awal/akhir dan ubah ke uppercase
    text = re.sub(r'[^A-Z0-9\s]', '', text)  #hapus semua karakter selain huruf, angka, dan spasi
    text = re.sub(r'\s+', ' ', text).strip()  #normalisasi spasi ganda menjadi spasi tunggal

    #pastikan format LBN dan LN dipisah dari angka berikutnya dengan spasi
    text = re.sub(r'^(LBN)(\d)', r'\1 \2', text)  #pisahkan LBN dari angka: "LBN1" -> "LBN 1"
    text = re.sub(r'^(LN\d)(\d)', r'\1 \2', text)  #pisahkan LN + size dari angka: "LN3600" -> "LN3 600"
    text = re.sub(r'([A-Z0-9])(ISS)$', r'\1 \2', text)  #pisahkan unit/suffix dari ISS: "600AISS" -> "600A ISS"
    text = re.sub(r'\s+', ' ', text).strip()  #hapus spasi ganda yang mungkin muncul setelah koreksi regex

    tokens = text.split()  #split text menjadi list token berdasarkan spasi

    if len(tokens) == 0:  #cek apakah ada token
        return text  #jika tidak ada token, kembalikan text original

    corrected_tokens = []  #list untuk menyimpan token yang sudah dikoreksi

    for i, token in enumerate(tokens):  #iterasi setiap token dengan index
        if i == 0:  #jika token pertama (prefix LBN/LN)
            #token pertama: perbaiki karakter L, B, N di posisi awal (prefix LBN/LN)
            corrected = ""  #variable untuk menyimpan token yang sudah dikoreksi
            for j, char in enumerate(token):  #iterasi setiap karakter dalam token
                if j == 0:  #posisi karakter pertama
                    #posisi pertama: harus 'L'
                    if char in ['1', 'I', 'l']:  #jika huruf/angka mirip L
                        corrected += 'L'  #konversi ke L
                    else:  #jika bukan
                        corrected += char  #gunakan apa adanya
                elif j == 1:  #posisi karakter kedua
                    #posisi kedua: harus 'B' atau 'N'
                    if char == '8':  #jika angka 8 (mirip B)
                        corrected += 'B'  #konversi ke B
                    elif char in ['H', 'M', 'I1']:  #jika huruf mirip N
                        corrected += 'N'  #konversi ke N
                    else:  #jika bukan
                        corrected += char  #gunakan apa adanya
                elif j == 2:  #posisi karakter ketiga
                    prefix_so_far = corrected  #ambil prefix yang sudah dikumpulkan
                    if prefix_so_far == 'LB':  #jika prefix adalah LB
                        #posisi ketiga setelah 'LB': harus 'N'
                        if char in ['H', 'M']:  #jika huruf mirip N
                            corrected += 'N'  #konversi ke N
                        else:  #jika bukan
                            corrected += char  #gunakan apa adanya
                    else:  #jika prefix adalah LN
                        #posisi ketiga setelah 'LN': harus angka (nomor ukuran 0-6)
                        digit_map = {'O': '0', 'Q': '0', 'I': '1', 'L': '1', 'Z': '2', 'S': '5', 'G': '6', 'B': '8'}  #mapping huruf mirip ke angka
                        corrected += digit_map.get(char, char)  #konversi atau gunakan apa adanya
                else:  #karakter setelahnya
                    corrected += char  #tambahkan apa adanya

            corrected_tokens.append(corrected)  #tambahkan token yang sudah dikoreksi ke list

        elif i == 1:  #jika token kedua (kapasitas)
            #token kedua: bagian kapasitas (angka + suffix huruf opsional seperti 'A')
            digit_map = {'O': '0', 'Q': '0', 'I': '1', 'L': '1', 'Z': '2', 'S': '5', 'G': '6', 'B': '8'}  #mapping huruf mirip ke angka untuk kapasitas

            corrected = ""  #variable untuk menyimpan token yang sudah dikoreksi
            for j, char in enumerate(token):  #iterasi setiap karakter dalam token
                is_last = (j == len(token) - 1)  #cek apakah ini karakter terakhir
                if char.isdigit():  #jika karakter adalah angka
                    corrected += char  #tambahkan langsung
                elif is_last and char.isalpha():  #jika karakter terakhir dan huruf
                    #karakter huruf terakhir = suffix unit (biasanya 'A'), koreksi '4' -> 'A'
                    if char in ['4']:  #jika angka 4 yang mirip A
                        corrected += 'A'  #konversi ke A
                    else:  #jika huruf lain
                        corrected += char  #gunakan apa adanya
                elif char in digit_map:  #jika huruf yang dalam mapping
                    corrected += digit_map[char]  #konversi huruf-mirip-angka ke angka yang sesuai
                else:  #karakter lain
                    corrected += char  #tambahkan apa adanya

            corrected_tokens.append(corrected)  #tambahkan token yang sudah dikoreksi ke list

        elif i == 2:  #jika token ketiga (ISS atau suffix)
            #token ketiga: hanya bisa berupa "ISS", normalisasi berbagai variasi penulisannya
            corrected = token  #default gunakan token apa adanya
            token_normalized = token.replace('5', 'S').replace('1', 'I').replace('0', 'O')  #normalisasi huruf mirip
            if token_normalized == 'ISS' or token in ['I55', 'IS5', 'I5S', '155', 'ISS']:  #cek apakah token adalah variasi ISS
                corrected = 'ISS'  #normalisasi ke bentuk standar ISS

            corrected_tokens.append(corrected)  #tambahkan token yang sudah dikoreksi ke list

        else:  #token di posisi lain
            corrected_tokens.append(token)  #token di luar 3 posisi utama diteruskan apa adanya

    result = ' '.join(corrected_tokens)  #gabung semua token yang sudah dikoreksi dengan spasi
    result = re.sub(r'\s+', ' ', result).strip()  #normalisasi spasi dan hapus spasi awal/akhir
    return result  #kembalikan hasil koreksi DIN


#fungsi dispatcher yaitu untuk pilih fungsi koreksi ocr yang sesuai berdasarkan preset aktif
def fix_common_ocr_errors(text, preset):  #fungsi dispatcher untuk memilih fungsi koreksi berdasarkan preset
    if preset == "JIS":  #jika preset adalah JIS
        return fix_common_ocr_errors_jis(text)  #panggil fungsi koreksi JIS
    elif preset == "DIN":  #jika preset adalah DIN
        return fix_common_ocr_errors_din(text)  #panggil fungsi koreksi DIN
    else:  #jika preset tidak dikenali
        return fix_common_ocr_errors_jis(text)  #fallback ke JIS jika preset tidak dikenali

#untuk konversi frame ke gambar biner (edge detection) untuk disimpan sebagai bukti deteksi
def convert_frame_to_binary(frame):  #fungsi untuk konversi frame ke gambar binary dengan edge detection
    return apply_edge_detection(frame)  #kembalikan hasil edge detection dari apply_edge_detection

#untuk mencoba mendapatkan nama asli kamera dari sistem operasi berdasarkan index-nya
#mengembalikan none jika nama tidak berhasil didapatkan
def get_camera_name(index):  #fungsi untuk mendapatkan nama device kamera berdasarkan index
    #ambil nama device kamera berdasarkan index OpenCV/DirectShow.
    #menggunakan pygrabber (jika tersedia) yang enumerate identik dengan OpenCV CAP_DSHOW.
    import platform  #import platform untuk deteksi OS
    if platform.system() != "Windows":  #cek apakah OS adalah Windows
        return None  #jika bukan Windows, return None

    #method 1: pygrabber — enumerate DirectShow persis seperti OpenCV
    try:  #coba method 1 menggunakan pygrabber
        from pygrabber.dshow_graph import FilterGraph  #import FilterGraph dari pygrabber
        graph = FilterGraph()  #buat instance FilterGraph
        devices = graph.get_input_devices()  #ambil list nama device kamera
        if 0 <= index < len(devices):  #cek apakah index valid
            return devices[index]  #kembalikan nama device pada index tersebut
    except ImportError:  #jika pygrabber tidak terinstall
        pass  #lanjut ke method berikutnya
    except Exception:  #jika ada error lain
        pass  #lanjut ke method berikutnya

    #method 2: PowerShell tanpa sort — urutan natural = urutan registrasi DirectShow
    try:  #coba method 2 menggunakan PowerShell
        import subprocess  #import subprocess untuk eksekusi command
        #tidak pakai Sort-Object agar urutan DirectShow terjaga
        cmd = (  #command PowerShell untuk get kamera list
            'powershell -NoProfile -Command "'
            'Get-PnpDevice -Class Camera -Status OK | '
            'Select-Object -ExpandProperty FriendlyName"'
        )
        result = subprocess.check_output(cmd, shell=True, timeout=5).decode('utf-8', errors='ignore')  #eksekusi command dan decode output
        names = [line.strip() for line in result.splitlines() if line.strip()]  #parse output menjadi list nama
        if 0 <= index < len(names):  #cek apakah index valid
            return names[index]  #kembalikan nama kamera pada index
    except Exception:  #jika PowerShell method gagal
        pass  #lanjut ke method berikutnya

    #method 3: fallback WMI tanpa Sort
    try:  #coba method 3 menggunakan WMI
        import subprocess  #import subprocess untuk eksekusi command
        cmd = (  #command PowerShell untuk get kamera list via WMI
            'powershell -NoProfile -Command "'
            'Get-WmiObject Win32_PnPEntity | '
            "Where-Object { $_.PNPClass -eq 'Camera' -or $_.PNPClass -eq 'Image' } | "
            'Select-Object -ExpandProperty Name"'
        )
        result = subprocess.check_output(cmd, shell=True, timeout=5).decode('utf-8', errors='ignore')  #eksekusi command dan decode output
        names = [line.strip() for line in result.splitlines() if line.strip()]  #parse output menjadi list nama
        if 0 <= index < len(names):  #cek apakah index valid
            return names[index]  #kembalikan nama kamera pada index
    except Exception:  #jika WMI method gagal
        pass  #tidak ada method lagi

    return None  #kembalikan None jika semua method gagal

#untuk mendeteksi semua kamera yang tersedia di sistem dan mengembalikan daftar infonya
#mencoba membuka setiap kamera dari index 0 hingga max_cameras dan memvalidasi dengan membaca frame
def get_available_cameras(max_cameras=5):  #fungsi untuk scan dan list semua kamera yang tersedia
    import platform  #import platform untuk deteksi OS
    available_cameras = []  #list untuk menyimpan info kamera yang ditemukan
    #gunakan backend yang sama dengan ocr.py (CAP_DSHOW di Windows)
    #agar index kamera konsisten antara dropdown dan saat kamera dibuka
    use_dshow = platform.system() == "Windows"  #cek apakah OS Windows untuk pakai CAP_DSHOW

    for i in range(max_cameras):  #iterasi dari index 0 hingga max_cameras-1
        cap = None  #variable untuk VideoCapture object
        try:  #coba buka kamera di index i
            cap = cv2.VideoCapture(i + cv2.CAP_DSHOW) if use_dshow else cv2.VideoCapture(i, cv2.CAP_ANY)  #buka kamera dengan backend sesuai OS
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 1000)  #set timeout 1 detik untuk open kamera
            if cap.isOpened():  #cek apakah kamera berhasil dibuka
                ret, test_frame = cap.read()  #coba baca satu frame untuk validasi kamera berfungsi

                if ret and test_frame is not None:  #cek apakah frame berhasil dibaca
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))  #ambil lebar frame (width)
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))  #ambil tinggi frame (height)

                    if w > 0 and h > 0:  #cek apakah dimensi valid (width dan height > 0)
                        #ambil nama asli device dari OS (urutan DirectShow, sama dengan OpenCV)
                        device_name = get_camera_name(i)  #panggil get_camera_name untuk ambil nama device
                        if device_name:  #jika nama device berhasil diambil
                            camera_name = f"{device_name} ({w}x{h})"  #format nama dengan resolusi
                        else:  #jika nama device tidak bisa diambil
                            #fallback jika nama tidak bisa dibaca
                            label = "Internal" if i == 0 else "External"  #tentukan label berdasarkan index
                            camera_name = f"Camera {i} ({label}) - {w}x{h}"  #format nama fallback

                        available_cameras.append({  #tambah info kamera ke list
                            'index': i,  #index kamera
                            'name': camera_name,  #nama display kamera
                            'width': w,  #lebar frame
                            'height': h,  #tinggi frame
                            'device_name': device_name  #nama device asli
                        })

        except Exception as e:  #jika ada error saat open kamera
            pass  #lewati kamera yang tidak bisa dibuka

        finally:  #pastikan resource selalu dilepas
            if cap is not None:  #cek apakah cap object ada
                try:
                    cap.release()  #lepaskan resource kamera
                except:  #jika error saat release
                    pass  #abaikan error

    return available_cameras  #kembalikan list kamera yang ditemukan

#untuk membuat direktori penyimpanan gambar dan Excel jika belum ada
def create_directories():  #fungsi untuk membuat direktori yang diperlukan jika belum ada
    from config import FILE_DIR, IMAGE_DIR, EXCEL_DIR  #import path direktori dari config
    os.makedirs(FILE_DIR,  exist_ok=True)  #buat folder utama 'file' pembungkus images & file_excel
    os.makedirs(IMAGE_DIR, exist_ok=True)  #buat folder file/images untuk menyimpan foto deteksi
    os.makedirs(EXCEL_DIR, exist_ok=True)  #buat folder file/file_excel untuk menyimpan export Excel

#untuk menghapus daftar file sementara dari disk (digunakan saat cleanup)
def cleanup_temp_files(temp_files_list):  #fungsi untuk menghapus list file sementara
    for t_path in temp_files_list:  #iterasi setiap file path dalam list
        if os.path.exists(t_path):  #cek apakah file ada di disk
            try:
                os.remove(t_path)  #hapus file
            except:  #jika ada error saat delete
                pass  #abaikan jika file tidak bisa dihapus (mungkin sedang digunakan)