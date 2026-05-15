//state alarm: menyimpan status alarm, mute flag, dan audio object untuk mengelola alarm not ok
const AlarmState = {
  enabled: true,       //alarm aktif secara default, dapat diubah user via setting
  muted: false,        //sementara dibisukan oleh user via toggle mute button
  audio: null,         //objek Audio untuk file alarm.mp3 dimuat dari /static/audio/alarm.mp3
  alarmInterval: null,  //timeout otomatis stop alarm setelah 10 detik berlangsung
  alertShown: false,   //apakah banner peringatan not ok sedang tampil di atas layar
  alarmActive: false,  //true selama periode 10 detik alarm berlangsung, false setelah timeout
};

//inisialisasi objek audio dari file lokal alarm.mp3, cache result agar hanya dibuat sekali saja
function _getAlarmAudio() {
  if (!AlarmState.audio) {
    AlarmState.audio = new Audio('/static/audio/alarm.mp3'); //buat audio element baru
    AlarmState.audio.preload = 'auto'; //preload agar siap diputar dengan segera
  }
  return AlarmState.audio; //return cached audio object
}

//putar file alarm.mp3 dari awal (reseet position ke 0 sebelum play)
function _playAlarmSound() {
  try {
    const audio = _getAlarmAudio();
    audio.currentTime = 0; //reset ke awal file
    audio.play().catch(() => {}); //play audio, abaikan error jika ada
  } catch(e) {}
}

//pause audio di posisi sekarang tanpa reset currentTime (agar bisa dilanjutkan nanti)
function _pauseAlarmSound() {
  try {
    if (AlarmState.audio) AlarmState.audio.pause(); //pause tanpa reset position
  } catch(e) {}
}

//lanjutkan audio dari posisi terakhir sebelum di-pause (resume di posisi yang sama)
function _resumeAlarmSound() {
  try {
    const audio = _getAlarmAudio();
    audio.play().catch(() => {}); //play lanjutkan dari posisi terakhir
  } catch(e) {}
}

//hentikan audio sepenuhnya dan reset posisi ke awal (dipakai saat alarm benar-benar selesai)
function _stopAlarmSound() {
  try {
    if (AlarmState.audio) {
      AlarmState.audio.pause(); //pause playback
      AlarmState.audio.currentTime = 0; //reset ke posisi awal
    }
  } catch(e) {}
}

//mulai alarm: putar audio sekali penuh (10 detik) lalu berhenti otomatis
function triggerNotOkAlarm(code) {
  if (!AlarmState.enabled) return;

  //tampilkan banner peringatan Not OK
  _showNotOkBanner(code);

  //tampilkan flash merah di video feed
  triggerFlash('notok');

  //hentikan alarm sebelumnya jika masih berjalan
  stopNotOkAlarm();

  //reset muted agar alarm selalu bunyi di awal kejadian Not OK baru
  AlarmState.muted = false;
  AlarmState.alarmActive = true;
  _updateMuteBtn();

  //putar audio dari awal
  _playAlarmSound();

  //hentikan otomatis setelah 10 detik (sesuai durasi audio)
  AlarmState.alarmInterval = setTimeout(() => stopNotOkAlarm(), 10000);
}

//hentikan alarm sepenuhnya: stop timeout, stop audio, reset state
function stopNotOkAlarm() {
  clearTimeout(AlarmState.alarmInterval);
  AlarmState.alarmInterval = null;
  AlarmState.alarmActive = false;  //tandai alarm sudah tidak aktif
  _stopAlarmSound();
  _updateMuteBtn();  //update tombol agar disable setelah 10 detik
}

//toggle mute: pause atau resume audio di posisi yang sama
function toggleMute() {
  //jangan lakukan apapun jika periode 10 detik sudah habis
  if (!AlarmState.alarmActive) return;

  AlarmState.muted = !AlarmState.muted;
  if (AlarmState.muted) {
    _pauseAlarmSound();   //pause di posisi sekarang
  } else {
    _resumeAlarmSound();  //lanjutkan dari posisi terakhir
  }
  _updateMuteBtn();
}

//update tampilan tombol mute sesuai state saat ini
function _updateMuteBtn() {
  const btn = document.querySelector('.notok-banner-mute');
  if (!btn) return;
  if (!AlarmState.alarmActive) {
    //alarm sudah selesai — nonaktifkan tombol agar tidak bisa diklik
    btn.disabled = true;
    btn.style.opacity = '0.4';
    btn.style.cursor  = 'not-allowed';
    btn.innerHTML = '<i class="bi bi-volume-mute-fill"></i> Selesai';
  } else if (AlarmState.muted) {
    btn.disabled = false;
    btn.style.opacity = '';
    btn.style.cursor  = '';
    btn.innerHTML = '<i class="bi bi-volume-mute-fill"></i> Nyalakan';
  } else {
    btn.disabled = false;
    btn.style.opacity = '';
    btn.style.cursor  = '';
    btn.innerHTML = '<i class="bi bi-volume-up-fill"></i> Bisukan';
  }
}

//tampilkan banner peringatan merah di tengah atas layar
function _showNotOkBanner(code) {
  let banner = document.getElementById('notok-alarm-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'notok-alarm-banner';
    document.body.appendChild(banner);
  }
  banner.innerHTML = `
    <div class="notok-banner-inner">
      <span class="notok-banner-icon"><i class="bi bi-exclamation-triangle-fill"></i></span>
      <div class="notok-banner-text">
        <strong>SCAN NOT OK</strong>
        <span>Kode terdeteksi tidak sesuai: <b>${code || '—'}</b></span>
      </div>
      <button class="notok-banner-mute" onclick="toggleMute();" title="Bisukan / Nyalakan alarm"><i class="bi bi-volume-up-fill"></i> Bisukan</button>
      <button class="notok-banner-close" onclick="_hideNotOkBanner(); stopNotOkAlarm();" title="Tutup"><i class="bi bi-x-lg"></i></button>
    </div>`;
  banner.classList.remove('notok-banner-hide');
  banner.classList.add('notok-banner-show');
  AlarmState.alertShown = true;
}

function _hideNotOkBanner() {
  const banner = document.getElementById('notok-alarm-banner');
  if (banner) { banner.classList.remove('notok-banner-show'); banner.classList.add('notok-banner-hide'); }
  AlarmState.alertShown = false;
  AlarmState.muted = false;

  //sembunyikan result-badge jika status saat ini adalah not ok
  const resultStatus = document.getElementById('result-status');
  const resultBadge  = document.getElementById('result-badge');
  if (resultStatus && resultBadge && resultStatus.textContent.trim() === 'NOT OK') {
    resultBadge.style.display = 'none';
  }
}

//preload audio alarm saat halaman sudah siap agar tidak ada jeda saat pertama bunyi
window.addEventListener('DOMContentLoaded', () => { _getAlarmAudio(); });

// ── STATE MOTION & ANTRIAN KARTON ───────────────────────────────────────────
// _motionActive : true jika motion sedang terdeteksi (karton ada di depan kamera)
// _scanInQueue  : true jika OCR sedang berjalan (scan_start terkirim, result belum)
// Keduanya dipakai untuk skip alarm/banner/badge saat karton berikutnya sudah datang
let _motionActive = false;
let _scanInQueue  = false;
// ─────────────────────────────────────────────────────────────────────────────

//buat string tanggal hari ini dalam format YYYY-MM-DD (dipakai sebagai bagian dari key penyimpanan)
function todayStr(){ const n=new Date(); return `${n.getFullYear()}-${String(n.getMonth()+1).padStart(2,'0')}-${String(n.getDate()).padStart(2,'0')}`; }

//buat key unik untuk menyimpan qty plan per label per hari di localStorage browser
function qtyKey(label){ return `qty_plan:${label}:${todayStr()}`; }

//ambil nilai qty plan yang tersimpan di browser untuk label tertentu hari ini
function loadQtyForLabel(label){
  if(!label) return 0;
  const stored = localStorage.getItem(qtyKey(label));
  return stored ? parseInt(stored)||0 : 0;
}

//simpan nilai qty plan ke browser untuk label tertentu (hapus jika nilai 0 agar tidak menumpuk)
function saveQtyForLabel(label, qty){
  if(!label) return;
  if(qty > 0) localStorage.setItem(qtyKey(label), qty);
  else localStorage.removeItem(qtyKey(label));
}

//S adalah "state" halaman: menyimpan semua kondisi tampilan yang sedang aktif
const S = {
  preset:'JIS', label:'', running:false,
  jis:[], din:[], months:[], roi_options:[],
  records:[], sel:new Set(), xrange:'Today',
  exportCancelling: false,
  qty_plan: 0,   //nilai qty plan dari setting
  roi: 'Full Frame (No ROI)',  //mode roi aktif
  shift: 0,      //0=semua, 1=shift1, 2=shift2, 3=shift3
};

//hubungkan ke server via socketio untuk komunikasi realtime (kamera streaming, notifikasi, dll)
const io_socket = io();

//saat pertama terhubung, server mengirim data awal untuk mengisi tampilan halaman
io_socket.on('init_data', d => {
  S.running=d.running||false; S.preset=d.preset||'JIS'; S.label=d.label||'';
  S.shift = autoDetectShift();
  _lastShift = S.shift;
  syncStartBtn(); setCamBadge(S.running); updatePresetBadge(S.preset); renderTable(d.records||[]);
  syncTopbarLabel(); updateShiftBadge(S.shift);
});

//saat ada frame baru dari kamera, tampilkan gambarnya di halaman
io_socket.on('frame', d => {
  hide('video-ph'); hide('scan-preview');
  const f=el('video-feed'); f.src='data:image/jpeg;base64,'+d.img; show(f);
  triggerScanFlash();
});

//saat kode baterai terdeteksi, tampilkan hasilnya dan perbarui tabel data
io_socket.on('code_detected', d => {
  const msg=d.message||'', recs=d.records||[];
  resetScanBtn();
  _scanInQueue = false;

  // Karton berikutnya sudah ter-scan → dismiss alarm, banner, dan badge lama
  // sebelum menampilkan hasil baru
  if (AlarmState.alarmActive || AlarmState.alertShown) {
    _hideNotOkBanner();
    stopNotOkAlarm();
  }
  if (_badgeTimer) { clearTimeout(_badgeTimer); _badgeTimer = null; }
  const badge = el('result-badge');
  if (badge) badge.style.display = 'none';

  if(msg==='FAILED') { toast('Gagal','Tidak ada label terdeteksi.','danger'); showBadge('—','FAILED','red'); }
  else if(msg.startsWith('ERROR:')) { toast('Error',msg.slice(6),'danger'); showBadge('ERR','Error','red'); }
  else {
    //cari record terbaru yang cocok dengan kode yang baru saja discan
    //recs diurutkan ASC dari server, jadi cari dari belakang agar dapat yang paling baru
    let latestRec = null;
    for (let i = recs.length - 1; i >= 0; i--) {
      if (recs[i].code === msg || recs[i].target === msg) {
        latestRec = recs[i]; break;
      }
    }
    //fallback: jika tidak ditemukan berdasarkan code, ambil record terakhir saja
    if (!latestRec && recs.length > 0) latestRec = recs[recs.length - 1];

    const isNotOk = latestRec && latestRec.status === 'Not OK';
    if (isNotOk) {
      //tampilkan badge merah, flash merah, dan bunyikan alarm
      showBadge(msg, 'NOT OK', 'red');
      triggerNotOkAlarm(msg);
    } else {
      showBadge(msg,'DETECTED','green'); triggerFlash('ok');
    }
  }
  renderTable(recs);
});

//saat status kamera berubah (nyala/mati), update tampilan tombol dan indikator kamera
io_socket.on('camera_status', d => {
  S.running=d.active; syncStartBtn(); setCamBadge(d.active);
  if(!d.active){ hide(el('video-feed')); hide(el('scan-preview')); showEl('video-ph'); hide(el('scan-overlay')); el('ocr-status-indicator').style.display='none'; el('motion-indicator').style.display='none'; }
});

//saat ada teks ocr baru, tampilkan di kotak TEXT OUTPUT
io_socket.on('ocr_text', d => renderOcr(d.texts||[]));

//saat ocr mulai memproses frame, tampilkan indikator "Memproses..."
io_socket.on('scan_start', () => {
  _scanInQueue = true;
  const ind = el('ocr-status-indicator');
  if (!ind) return;
  ind.className = 'ocr-processing';
  ind.innerHTML = '<i class="bi bi-cpu" style="font-size:11px"></i> Memproses…';
  ind.style.display = 'flex';
});

//saat ocr selesai memproses, tampilkan indikator "Selesai" lalu sembunyikan setelah 1.5 detik
io_socket.on('scan_done', () => {
  _scanInQueue = false;
  const ind = el('ocr-status-indicator');
  if (!ind) return;
  ind.className = 'ocr-done';
  ind.innerHTML = '<i class="bi bi-check-circle" style="font-size:11px"></i> Selesai';
  ind.style.display = 'flex';
  setTimeout(() => { ind.style.display = 'none'; }, 1500);
});

//saat hari berganti dan data direset, kosongkan tabel
io_socket.on('data_reset', () => { renderTable([]); toast('Info','Data di-reset untuk hari baru.','info'); });

//handler disconnect WebSocket: tampilkan toast peringatan
io_socket.on('disconnect', () => {
  toast('Koneksi Terputus','Koneksi ke server terputus. Mencoba menghubungkan kembali…','warning');
});

//handler connect/reconnect WebSocket: auto-refresh data setelah reconnect
io_socket.on('connect', () => {
  //hanya auto-refresh jika bukan pertama kali (halaman sudah ter-inisialisasi)
  if (typeof S !== 'undefined' && S.records !== undefined) {
    refreshData().catch(()=>{});
    toast('Terhubung','Koneksi kembali. Data diperbarui.','success');
  }
});

//tampilkan atau sembunyikan indikator motion detection sesuai status yang dikirim server
io_socket.on('motion', d => {
  const ind = el('motion-indicator');
  if (!ind) return;
  ind.style.display = d.detected ? 'flex' : 'none';
  _motionActive = d.detected;
});

//update progress bar export saat server melaporkan kemajuan proses export
io_socket.on('export_progress', d => {
  const p = d.total > 0 ? Math.round(d.current / d.total * 100) : 0;
  //update tombol btn-export-do menjadi "Batal X%" saat export biasa berjalan
  if(!(typeof histExportState !== 'undefined' && histExportState.running)){
    const b = el('btn-export-do');
    if(b && b.dataset.running === '1'){
      b.style.background = `linear-gradient(90deg,#dc2626 ${p}%,rgba(231,25,25,.83) ${p}%)`;
      b.style.color = '#fff';
      b.style.border = 'none';
      b.innerHTML = `Batal ${p}%`;
    }
  }
  //update tombol Batal di history jika export dipicu dari sana
  if(typeof histExportState !== 'undefined' && histExportState.running){
    const btn = el('hist-export-btn');
    if(btn) histExportSetProgress(p);
  }
  //jika msg mengandung "[Auto-Export" → update progress bar di banner auto-export
  if(d.msg && d.msg.includes('[Auto-Export')){
    const banner = el('auto-export-banner');
    const wrap   = el('aeb-progress-wrap');
    const bar    = el('aeb-progress-bar');
    const label  = el('aeb-progress-label');
    if(banner && !banner.classList.contains('show')) banner.classList.add('show');
    if(wrap)  wrap.classList.add('show');
    if(bar)   bar.style.width = p + '%';
    if(label) label.textContent = p + '%';
    const msgEl = el('aeb-msg');
    if(msgEl) msgEl.textContent = d.msg || '';
    const titleEl = el('aeb-title');
    if(titleEl) titleEl.textContent = 'Sedang Export…';
    const iconEl = el('aeb-icon');
    if(iconEl){ iconEl.className = 'bi bi-arrow-clockwise aeb-icon'; iconEl.style.color = '#f59e0b'; }
  }
});;

//saat export selesai, tampilkan notifikasi dengan tombol buka folder
io_socket.on('export_done', d => {
  //tangani cancel dari history scan
  if(S.exportCancelling && typeof histExportState !== 'undefined' && histExportState.running){
    S.exportCancelling = false;
    histExportReset();
    return;
  }

  //tangani cancel dari modal biasa
  if(S.exportCancelling){ S.exportCancelling=false; return; }

  //tangani export yang dipicu dari history scan
  if(typeof histExportState !== 'undefined' && histExportState.running){
    histExportReset();
    if(d.ok && d.path){
      const fn = d.path.split(/[\\/]/).pop();
      toastWithFolder('Berhasil', 'File disimpan: ' + fn, d.path);
    } else if(d.no_data){
      toast('Export Gagal','Tidak ada data untuk label ini.','warning');
    } else {
      toast('Export Gagal', d.msg || 'Kesalahan.','danger');
    }
    return;
  }

  //tangani export dari modal export biasa
  exportDoReset();
  if(d.ok && d.path){
    const fn=d.path.split(/[\\\/]/).pop();
    bootstrap.Modal.getInstance(el('mExport'))?.hide();
    toastWithFolder('Berhasil', 'File disimpan: ' + fn, d.path);
  }
  else if(d.no_data){ toast('Export Gagal','Gagal Export, Tidak ada data !','warning'); }
  else toast('Export Gagal', d.msg||'Kesalahan.','danger');
});;;

//fungsi inisialisasi halaman: jalankan jam, muat label, kamera, dan status saat halaman pertama dibuka
async function init(){
  startClock();
  //default: light mode. dark hanya aktif jika user sebelumnya memilih dark
  document.documentElement.classList.remove('dark');
  if(localStorage.getItem('theme')==='dark'){
    document.documentElement.classList.add('dark');
    el('theme-icon').className='bi bi-sun-fill';
  } else {
    localStorage.setItem('theme','light');
    el('theme-icon').className='bi bi-moon-fill';
  }
  //muat daftar label JIS/DIN dan nama bulan dari server
  const ld=await (await fetch('/api/labels')).json();
  S.jis=ld.jis||[]; S.din=ld.din||[]; S.months=ld.months||[];
  populateXMonth(); populateXLabel();
  await loadCams();
  //ambil state aplikasi saat ini dari server dan terapkan ke tampilan
  const sd=await (await fetch('/api/state')).json();
  S.preset=sd.preset||'JIS'; S.label=sd.target_label||'';
  S.qty_plan=sd.qty_plan||0;
  S.shift = autoDetectShift();
  _lastShift = S.shift;
  updatePresetBadge(S.preset);
  syncTopbarLabel();
  updateShiftBadge(S.shift);
  //jika ada label aktif, coba ambil qty plan dari localStorage (lebih akurat per-label)
  if(S.label){
    const storedQty = loadQtyForLabel(S.label);
    if(storedQty > 0){ S.qty_plan = storedQty; }
  }
  S.roi=sd.roi_mode||'Full Frame (No ROI)';
  el('s-preset').value=S.preset; syncSettingLabel();
  el('s-qty-plan').value=S.qty_plan;
  //muat daftar pilihan roi dari server lalu isi dropdown
  const rd=await (await fetch('/api/roi_options')).json();
  S.roi_options=rd.roi_options||[];
  populateRoiSelect();
  el('s-roi').value=S.roi;
  await refreshData();

  //mulai cek apakah model ocr sudah siap
  pollOcrReady();
}

//cek secara berkala apakah model ocr sudah siap, disable tombol start sampai siap
async function pollOcrReady(){
  const b = el('btn-start');
  let ready = false;
  try {
    const r = await (await fetch('/api/ocr/ready')).json();
    ready = r.ready || false;
  } catch(e) { ready = false; }

  if(!ready && !S.running){
    //tampilkan status "memuat ocr" di tombol start selama model belum siap
    b.disabled = true;
    b.className = 'btn-start-footer';
    b.style.opacity = '0.6';
    b.innerHTML = '<span class="start-val-text"><span class="ocr-loading-dot"></span> Memuat…</span>';
    setTimeout(pollOcrReady, 1500);  //cek lagi setelah 1.5 detik
  } else {
    if(!S.running){ b.disabled = false; syncStartBtn(); }
  }
}

//variabel untuk mengatur jeda antar efek flash scan
let _lastScanFlash = 0;
let _scanFlashTimer = null;

//tampilkan efek kilat (flash) di overlay kamera saat deteksi berhasil
function triggerFlash(type) {
  const overlay = el('flash-overlay');
  const border  = el('shutter-border');
  if (!overlay) return;

  //hapus class lama dan paksa browser menghitung ulang agar animasi bisa diulang
  overlay.className = '';
  border.className  = '';
  void overlay.offsetWidth;

  if (type === 'notok') {
    //flash merah untuk scan Not OK
    overlay.classList.add('do-flash-notok');
    border.classList.add('active-notok');
    setTimeout(() => { border.className = ''; }, 900);
  } else {
    //flash putih untuk deteksi berhasil
    overlay.classList.add('do-flash-ok');
    border.classList.add('active-ok');
    setTimeout(() => { border.className = ''; }, 900);
  }
}

//tampilkan efek flash biru tipis setiap beberapa frame sebagai tanda kamera aktif scanning
function triggerScanFlash() {
  const now = Date.now();
  //batasi efek scan flash maksimal 1x tiap 2 detik agar tidak mengganggu
  if (now - _lastScanFlash < 2000) return;
  _lastScanFlash = now;

  const overlay = el('flash-overlay');
  if (!overlay || overlay.classList.contains('do-flash-ok') || overlay.classList.contains('do-flash-notok')) return;
  overlay.className = '';
  void overlay.offsetWidth;
  overlay.classList.add('do-flash-scan');
  setTimeout(() => { if(overlay.classList.contains('do-flash-scan')) overlay.className=''; }, 400);
}

//tampilkan atau sembunyikan indikator "Scanning..." di pojok video
function setScanIndicator(visible) {
  const ind = el('scan-indicator');
  if (!ind) return;
  ind.style.display = visible ? 'flex' : 'none';
}

//nama hari dan bulan dalam bahasa Indonesia untuk ditampilkan di footer
const DN=['Minggu','Senin','Selasa','Rabu','Kamis','Jumat','Sabtu'];
const MN=['Januari','Februari','Maret','April','Mei','Juni','Juli','Agustus','September','Oktober','November','Desember'];
const _pageStart = Date.now();

//jalankan jam digital di footer yang diperbarui setiap detik
function startClock(){
  const tick=()=>{
    const n=new Date();
    const timeStr = n.toLocaleTimeString('id-ID',{hour:'2-digit',minute:'2-digit',second:'2-digit'}).replace(/:/g,'.');
    const dateStr = `${DN[n.getDay()]}, ${n.getDate()} ${MN[n.getMonth()]} ${n.getFullYear()}`;
    el('ft-date').textContent = dateStr;
    //render hanya waktu di ft-time — shift badge sudah terpisah di topbar-date-row
    const ftTime = el('ft-time');
    if(ftTime){
      ftTime.textContent = timeStr;
    }
  };
  tick(); setInterval(tick, 1000);
}

//ganti tema tampilan antara mode terang (light) dan gelap (dark)
function toggleTheme(){
  const isDark=document.documentElement.classList.toggle('dark');
  el('theme-icon').className = isDark ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
  localStorage.setItem('theme', isDark ? 'dark' : 'light');
}

//muat daftar kamera yang tersedia ke dropdown dari API server (index OpenCV yang valid)
//nama label dari browser dipakai jika cocok berdasarkan urutan, tapi value selalu dari server
async function loadCams(){
  const s=el('s-cam'); s.innerHTML='';
  //ambil hanya dari server — index dan nama sudah diverifikasi OpenCV
  //jangan gunakan browser enumerateDevices karena urutannya berbeda dari OpenCV
  try {
    const d=await (await fetch('/api/cameras')).json();
    const cams = d.cameras || [];
    if(cams.length === 0){
      const o=document.createElement('option');
      o.value=0; o.textContent='Camera 0 (Default)';
      s.appendChild(o);
      return;
    }
    cams.forEach(c=>{
      const o=document.createElement('option');
      o.value=c.index;   //index OpenCV yang valid
      o.textContent=c.name;
      s.appendChild(o);
    });
  } catch(e){
    const o=document.createElement('option');
    o.value=0; o.textContent='Camera 0 (Default)';
    s.appendChild(o);
  }
}

//toggle: nyalakan kamera jika mati, matikan jika sedang nyala
async function toggleCamera(){
  if(S.running){
    const ok = await showConfirm('Apakah kamu yakin ingin <strong>menghentikan kamera</strong>?<br>Proses scan akan berhenti.', {
      title: 'Stop Kamera',
      icon: 'bi-camera-video-off-fill',
      okLabel: 'Stop',
    });
    if(!ok) return;
    await stopCam();
  } else {
    //tampilkan dialog konfirmasi ringkasan sebelum mulai kamera
    if(!validLabel(S.label,S.preset)){ toast('Perhatian','Pilih label terlebih dahulu.','warning'); return; }
    if(S.qty_plan <= 0){ toast('Perhatian','Isi QTY Plan terlebih dahulu di Setting.','warning'); return; }
    el('cc-label').textContent = S.label  || '—';
    el('cc-qty').textContent   = S.qty_plan > 0 ? S.qty_plan + ' pcs' : '—';
    new bootstrap.Modal(el('mCamConfirm')).show();
  }
}

//dipanggil saat user klik "Mulai" di dialog konfirmasi kamera
async function _doCamConfirmStart(){
  bootstrap.Modal.getInstance(el('mCamConfirm'))?.hide();
  await startCam();
}

//mulai kamera: validasi label dan qty plan, lalu kirim perintah ke server
async function startCam(){
  if(!validLabel(S.label,S.preset)){ toast('Perhatian','Pilih label terlebih dahulu.','warning'); return; }
  if(S.qty_plan <= 0){ toast('Perhatian','Isi QTY Plan terlebih dahulu di Setting.','warning'); return; }
  //tampilkan animasi loading di tombol saat menunggu server membuka kamera
  const b=el('btn-start');
  b.className='btn-start-footer';
  b.style.opacity='0.6';
  b.innerHTML='<span class="start-val-text"><span class="btn-loader"></span> Membuka…</span>';
  b.disabled=true;
  let r;
  try {
    r=await (await fetch('/api/camera/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      preset:S.preset,label:S.label,camera_index:Number(el('s-cam').value),
      edge_mode:el('chk-edge').checked,
      roi_mode:S.roi
    })})).json();
  } catch(e){
    toast('Error','Gagal menghubungi server.','danger');
    b.disabled=false; syncStartBtn(); return;
  }
  b.disabled=false;
  if(!r.ok){ toast('Error',r.msg,'danger'); syncStartBtn(); return; }
  hide(el('scan-preview')); hide(el('result-badge'));
  S.running=true; syncStartBtn(); lockSetting(true); showOverlay(); setScanIndicator(true);
}

//hentikan kamera dan kembalikan tampilan ke kondisi awal
async function stopCam(){
  await fetch('/api/camera/stop',{method:'POST'});
  S.running=false; syncStartBtn(); lockSetting(false); setScanIndicator(false);
}

//sinkronkan tampilan tombol start/stop sesuai status kamera saat ini
function syncStartBtn(){
  const b=el('btn-start');
  b.disabled=false;
  b.style.opacity='1';
  if(S.running){
    b.className='btn-start-footer active';
    b.innerHTML='<span class="start-val-text">STOP</span>';
  } else {
    b.className='btn-start-footer';
    b.innerHTML='<span class="start-val-text">START</span>';
  }
}

//setCamBadge dihapus (indikator kamera tidak ditampilkan)
function setCamBadge(on){}

//updatePresetBadge dihapus (badge preset tidak ditampilkan)
function updatePresetBadge(preset){}

//update nama label di topbar tengah
function syncTopbarLabel(){
  const el_lbl = document.getElementById('topbar-label-name');
  if(el_lbl) el_lbl.textContent = (S.label && S.label !== '—') ? S.label : '—';
}

//kunci atau buka tombol setting (setting tidak bisa diubah saat kamera sedang jalan)
function lockSetting(v){ el('btn-setting').disabled=v; }

//tampilkan overlay kecil di atas video yang menampilkan preset dan label aktif
function showOverlay(){ const o=el('scan-overlay'); o.textContent=S.label; show(o); }

//cek apakah label yang dipilih valid sesuai preset aktif (JIS atau DIN)
function validLabel(l,p){ if(!l||l==='Select Label . . .'||!l.trim()) return false; return (p==='DIN'?S.din:S.jis).slice(1).includes(l); }

//kirim perubahan opsi (binary color, split screen) ke server saat kamera sedang berjalan
async function onOptionChange(){
  if(!S.running) return;
  await fetch('/api/camera/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({preset:S.preset,label:S.label,edge_mode:el('chk-edge').checked})});
}

//buka dialog pilih file saat tombol "Scan from File" diklik (validasi dulu sebelum buka)
function clickScanFile(){
  if(!validLabel(S.label,S.preset)){ toast('Perhatian','Pilih label terlebih dahulu.','warning'); return; }
  if(S.qty_plan <= 0){ toast('Perhatian','Isi QTY Plan terlebih dahulu di Setting.','warning'); return; }
  if(S.running){ toast('Info','Stop kamera terlebih dahulu.','info'); return; }
  el('fhidden').click();
}

//saat user memilih file dari dialog, langsung jalankan scan
function onFileChange(e){ const f=e.target.files[0]; if(f){ doScan(f); e.target.value=''; } }

//efek visual saat file di-drag di atas area drop
function onDragOver(e){ e.preventDefault(); el('file-dropzone').classList.add('drag-over'); }
function onDragLeave(e){ el('file-dropzone').classList.remove('drag-over'); }

//tangani file yang di-drop ke area dropzone
function onFileDrop(e){ e.preventDefault(); el('file-dropzone').classList.remove('drag-over'); const f=e.dataTransfer.files[0]; if(f) doScan(f); }

//proses scan file: tampilkan preview foto, kirim file ke server, lalu tunggu hasilnya
async function doScan(file){
  //segera tampilkan foto yang dipilih sebagai preview sebelum scan selesai
  const reader=new FileReader();
  reader.onload=e=>{
    const p=el('scan-preview'); p.src=e.target.result; show(p);
    hide(el('video-feed')); hide(el('video-ph'));
  };
  reader.readAsDataURL(file);
  showBadge('Scanning…','Processing','amber');

  //kirim pengaturan terbaru ke server dulu sebelum scan
  await fetch('/api/camera/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({preset:S.preset,label:S.label,edge_mode:el('chk-edge').checked,roi_mode:S.roi})});
  const b=el('btn-scan'); b.innerHTML='<i class="bi bi-hourglass-split"></i> Scanning…'; b.disabled=true;
  const fd=new FormData(); fd.append('file',file);
  const r=await (await fetch('/api/scan/file',{method:'POST',body:fd})).json();
  if(!r.ok){ toast('Error',r.msg,'danger'); resetScanBtn(); }
}

//kembalikan tombol scan ke kondisi normal setelah proses selesai
function resetScanBtn(){ const b=el('btn-scan'); b.innerHTML='<i class="bi bi-image"></i> Scan from File'; b.disabled=false; }

//tampilkan badge hasil deteksi (kode, status, warna) dan sembunyikan otomatis setelah beberapa detik
let _badgeTimer = null;
function showBadge(code,status,color){
  const b=el('result-badge'), c=el('result-code'), s=el('result-status');
  const colors={green:'var(--green)',red:'var(--red)',amber:'var(--amber)'};
  c.textContent=code; c.style.color=colors[color]||'#fff'; s.textContent=status; show(b);
  if(_badgeTimer){ clearTimeout(_badgeTimer); _badgeTimer=null; }
  if(status==='DETECTED'){
    _badgeTimer=setTimeout(()=>{ hide(b); _badgeTimer=null; }, 3000);  //otomatis hilang setelah 3 detik
  }
}

//tampilkan teks hasil ocr mentah di kotak TEXT OUTPUT
function renderOcr(texts){
  const box=el('ocr-box'); box.innerHTML='';
  if(!texts.length){ box.innerHTML='<div class="ocr-item" style="color:var(--text-3)">Menunggu deteksi…</div>'; return; }
  texts.forEach(t=>{ const d=document.createElement('div'); d.className='ocr-item'; d.textContent=t; box.appendChild(d); });
}

//render tabel data deteksi: filter berdasarkan label aktif, hitung statistik, update footer
function renderTable(records){
  S.records=records||[]; S.sel.clear();
  const tbody=el('code-tbody'), cnt=el('data-count')||{textContent:''};

  //update statistik footer dari records saat ini
  if(!validLabel(S.label,S.preset)){
    tbody.innerHTML='<tr><td colspan="4" style="padding:28px;text-align:center;color:var(--text-3)">Pilih label terlebih dahulu</td></tr>';
    cnt.textContent='0 record';
    ['stat-total','stat-ok','stat-nok'].forEach(id=>el(id).textContent='0');
    el('ft-qty-progress').textContent='—';
    return;
  }

  //filter hanya record yang sesuai dengan label aktif, urutkan dari terbaru
  const filtered=[...S.records].reverse().filter(r=>(r.target||r.code)===S.label);
  cnt.textContent=filtered.length+' record';
  let ok=0,nok=0;
  if(!filtered.length){
    tbody.innerHTML='<tr><td colspan="4" style="padding:28px;text-align:center;color:var(--text-3)">Belum ada data untuk label ini</td></tr>';
  } else {
    tbody.innerHTML='';
    filtered.forEach(r=>{
      const tr=document.createElement('tr');
      const st=r.status||'OK';
      //baris berstatus Not OK diberi warna merah
      if(st==='Not OK'){ tr.classList.add('not-ok'); nok++; } else ok++;
      const time=(r.time||'').split(' ')[1]||'';
      const lbl=`${r.code} (${r.type})`;
      const badge=st==='OK'?'<span class="badge-ok">OK</span>':'<span class="badge-nok">Not OK</span>';
      const hasImg = !!(r.imgPath && r.imgPath.trim());
      const imgFilename = hasImg ? r.imgPath.split(/[\\\/]/).pop() : '';
      const fotoCell = hasImg
        ? `<button class="tbl-foto-btn" onclick="event.stopPropagation();openHistLightbox('/api/image/${imgFilename}','${r.code||''}','${r.time||''}')" title="Lihat foto"><i class="bi bi-image-fill"></i></button>`
        : `<i class="bi bi-image" style="opacity:.2;font-size:14px"></i>`;
      tr.innerHTML=`<td>${time}</td><td>${lbl}</td><td>${badge}</td><td style="text-align:center">${fotoCell}</td>`;
      //klik untuk pilih/batalkan pilihan baris (untuk hapus)
      tr.addEventListener('click',()=>{ tr.classList.toggle('selected'); tr.classList.contains('selected')?S.sel.add(r.id):S.sel.delete(r.id); });
      tbody.appendChild(tr);
    });
  }
  el('stat-total').textContent=filtered.length;
  el('stat-ok').textContent=ok; el('stat-nok').textContent=nok;

  //update progress qty plan di footer
  const plan = S.qty_plan > 0 ? S.qty_plan : '?';
  el('ft-qty-progress').textContent = S.qty_plan > 0 ? `${ok} / ${plan}` : `${ok} / —`;

  //warna biru jika sudah mencapai atau melewati plan
  const qtyEl = el('ft-qty-progress');
  if (S.qty_plan > 0 && ok >= S.qty_plan) {
    qtyEl.style.color = '#1245b4';
  } else {
    qtyEl.style.color = '';
  }

}

//ambil data terbaru dari server dan render ulang tabel
async function refreshData(){
  const d=await (await fetch("/api/data/today")).json();
  renderTable(d.records||[]);
  //terapkan ulang filter pencarian jika ada teks yang sedang diketik
  const q = el("table-search");
  if(q && q.value.trim()) filterTable(q.value);
}

//hapus baris yang dipilih setelah konfirmasi dari user
async function clearSelected(){
  if(!S.sel.size){ toast('Perhatian','Pilih data terlebih dahulu.','warning'); return; }
  const ok = await showConfirm(`Apakah kamu yakin ingin menghapus <strong>${S.sel.size} item</strong> yang dipilih? Tindakan ini tidak dapat dibatalkan.`);
  if(!ok) return;
  const d=await (await fetch('/api/data/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:[...S.sel]})})).json();
  if(d.ok){ toast('Sukses',`${S.sel.size} data dihapus.`,'success'); await refreshData(); } else toast('Error',d.msg,'danger');
}


//tampilkan dialog konfirmasi kustom (pengganti window.confirm bawaan browser)
let _confirmResolve = null;
//options: { title, icon (class bi-*), okLabel, okClass }
function showConfirm(msg, options){
  const opts = options || {};
  const title    = opts.title   || 'Hapus Data';
  const icon     = opts.icon    || 'bi-trash3-fill';
  const okLabel  = opts.okLabel || 'Hapus';
  const okClass  = opts.okClass || '';

  el('confirm-title').textContent    = title;
  el('confirm-icon').innerHTML       = `<i class="bi ${icon}"></i>`;
  el('confirm-ok').textContent       = okLabel;
  el('confirm-ok').className         = okClass ? okClass : '';
  el('confirm-msg').innerHTML        = msg;
  el('confirm-overlay').classList.add('show');
  return new Promise(res => { _confirmResolve = res; });
}

//tutup dialog konfirmasi dan kembalikan hasil pilihan user (true=ya, false=tidak)
function closeConfirm(result){
  el('confirm-overlay').classList.remove('show');
  if(_confirmResolve){ _confirmResolve(result); _confirmResolve=null; }
}

//tampilkan popup sukses di panel kiri yang otomatis hilang setelah 3.5 detik
let _st;

//isi dropdown roi di modal setting dengan pilihan yang didapat dari server
function populateRoiSelect(){
  const sel=el('s-roi'); sel.innerHTML='';
  S.roi_options.forEach(r=>{ const o=document.createElement('option'); o.value=r; o.textContent=r; sel.appendChild(o); });
}

//buka modal setting: isi nilai saat ini (preset, label, qty plan, roi)
function openSetting(){
  if(S.running){ toast('Perhatian','Stop kamera sebelum membuka Setting.','warning'); return; }
  el('s-preset').value=S.preset; syncSettingLabel();
  //tampilkan qty tersimpan untuk label aktif saat ini
  const storedQty = loadQtyForLabel(S.label);
  el('s-qty-plan').value = storedQty > 0 ? storedQty : S.qty_plan;
  el('s-roi').value=S.roi;
  new bootstrap.Modal(el('mSetting')).show();
}

//sinkronkan dropdown label di modal setting sesuai preset yang dipilih (JIS atau DIN)
function syncSettingLabel(){
  const types=el('s-preset').value==='DIN'?S.din:S.jis;
  const options=types.map(t=>({value:t, label:t, disabled:t==='Select Label . . .'}));
  ssPopulate('ss-label-wrap','s-label', options);
  const cur = S.label && types.includes(S.label) ? S.label : (types[1]||'');
  ssSetValue('ss-label-wrap','s-label', cur);
}

//simpan pengaturan baru dari modal setting dan kirim ke server
async function saveSetting(){
  const p=el('s-preset').value, l=el('s-label').value;
  if(!validLabel(l,p)){ toast('Perhatian','Pilih label yang valid.','warning'); return; }

  //simpan qty plan label lama ke localStorage sebelum pindah ke label baru
  if(S.label) saveQtyForLabel(S.label, S.qty_plan);

  S.preset=p; S.label=l;
  S.roi=el('s-roi').value;
  syncTopbarLabel();
  updatePresetBadge(S.preset);

  //ambil dan simpan nilai qty plan baru
  const qty = Math.max(0, parseInt(el('s-qty-plan').value)||0);
  S.qty_plan = qty;
  if(qty > 0) saveQtyForLabel(l, qty);

  //kirim qty plan dan pengaturan kamera baru ke server
  await fetch('/api/qty_plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({qty_plan:S.qty_plan})});
  await fetch('/api/camera/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({preset:S.preset,label:S.label,edge_mode:el('chk-edge').checked,roi_mode:S.roi})});
  await refreshData();
  bootstrap.Modal.getInstance(el('mSetting'))?.hide();
}

//buka modal export biasa: isi nilai default (preset, label, tanggal hari ini)
function openExport(){
  el('x-preset').value=S.preset; populateXLabel();
  const t=new Date().toISOString().split('T')[0]; el('x-start').value=t; el('x-end').value=t;
  new bootstrap.Modal(el('mExport')).show();
}

//isi dropdown bulan dan tahun di modal export dengan nilai yang relevan
function populateXMonth(){
  const ms=el('x-month'); ms.innerHTML=''; S.months.forEach(m=>{ const o=document.createElement('option'); o.value=m; o.textContent=m; ms.appendChild(o); });
  const miIdx=new Date().getMonth(); const cm=['Januari','Februari','Maret','April','Mei','Juni','Juli','Agustus','September','Oktober','November','Desember'][miIdx]; if([...ms.options].some(o=>o.value===cm)) ms.value=cm;
  const ys=el('x-year'); ys.innerHTML=''; const cy=new Date().getFullYear(); for(let y=cy;y>=cy-4;y--){ const o=document.createElement('option'); o.value=y; o.textContent=y; ys.appendChild(o); }
}

//isi dropdown label di modal export sesuai preset yang dipilih
function populateXLabel(){
  const pv=el('x-preset').value, act=pv==='Preset'?S.preset:pv, types=act==='DIN'?S.din:S.jis;
  const options=[{value:'All Label',label:'All Label'},...types.slice(1).map(t=>({value:t,label:t}))];
  ssPopulate('ss-xlabel-wrap','x-label', options);
  const cur = S.label && [...options].some(o=>o.value===S.label) ? S.label : 'All Label';
  ssSetValue('ss-xlabel-wrap','x-label', cur);
}

//simpan pilihan rentang tanggal saat radio button diubah
function onXDate(){ S.xrange=document.querySelector('input[name="xdate"]:checked').value; }

//aktifkan/nonaktifkan dropdown label di export
function onXLabelChk(){
  const disabled=!el('x-lchk').checked;
  const wrap=el('ss-xlabel-wrap');
  if(disabled){ wrap.style.opacity='.5'; wrap.style.pointerEvents='none'; }
  else { wrap.style.opacity=''; wrap.style.pointerEvents=''; }
}

//aktifkan mode filter per bulan, nonaktifkan mode filter per tanggal custom
function onXMonthChk(){
  const c=el('x-mchk').checked; el('x-month').disabled=!c; el('x-year').disabled=!c;
  if(c){ el('x-dchk').checked=false; onXDateChk_off(); el('rb-all').checked=true; el('rb-today').disabled=true; S.xrange='Month'; }
  else { el('rb-today').disabled=false; if(!el('x-dchk').checked){ el('rb-today').checked=true; S.xrange='Today'; } }
}

//aktifkan mode filter per rentang tanggal custom, nonaktifkan mode filter per bulan
function onXDateChk(){
  const c=el('x-dchk').checked; el('x-start').disabled=!c; el('x-end').disabled=!c;
  if(c){ el('x-mchk').checked=false; onXMonthChk_off(); el('rb-all').checked=true; el('rb-today').disabled=true; S.xrange='CustomDate'; }
  else { el('rb-today').disabled=false; if(!el('x-mchk').checked){ el('rb-today').checked=true; S.xrange='Today'; } }
}

//nonaktifkan dropdown bulan dan tahun (dipakai saat mode bulan dimatikan)
function onXMonthChk_off(){ el('x-month').disabled=true; el('x-year').disabled=true; }

//nonaktifkan input tanggal custom (dipakai saat mode tanggal custom dimatikan)
function onXDateChk_off(){ el('x-start').disabled=true; el('x-end').disabled=true; }

//kirim perintah export ke server berdasarkan filter yang dipilih di modal export
async function doExport(){
  const b=el('btn-export-do');
  //jika sedang berjalan, tombol berfungsi sebagai Batal
  if(S.exportCancelling === false && b.dataset.running === '1'){
    S.exportCancelling = true;
    fetch('/api/export/cancel',{method:'POST'}).catch(()=>{});
    exportDoReset();
    toast('Info','Export dibatalkan.','warning');
    return;
  }
  S.exportCancelling = false;

  //cek apakah user memilih "Semua Data" — tampilkan konfirmasi tambahan
  let range=S.xrange;
  if(el('x-mchk').checked) range='Month'; if(el('x-dchk').checked) range='CustomDate';
  if(!el('x-mchk').checked&&!el('x-dchk').checked) range=document.querySelector('input[name="xdate"]:checked').value;

  if(range === 'All'){
    const konfirmasi = await showConfirm(
      'Kamu memilih <strong>Semua Data</strong> tanpa filter tanggal.<br>Proses ini mungkin memakan waktu lama tergantung jumlah data.<br><br>Lanjutkan export semua data?',
      { title: 'Konfirmasi Export Semua Data', icon: 'bi-database-fill-exclamation', okLabel: 'Ya, Export Semua' }
    );
    if(!konfirmasi) return;
  }

  b.dataset.running = '1';
  b.style.background = 'linear-gradient(90deg,#dc2626 0%,rgba(231,25,25,.83) 0%)';
  b.style.color = '#fff';
  b.style.border = 'none';
  b.innerHTML = 'Batal 0%';
  const payload={date_range:range,preset:el('x-preset').value,label:el('x-lchk').checked?el('x-label').value:'All Label',month:el('x-month').value,year:el('x-year').value,start_date:el('x-start').value,end_date:el('x-end').value,qty_plan:S.qty_plan};
  const r=await (await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})).json();
  if(!r.ok){ toast('Error',r.msg,'danger'); exportDoReset(); }
}

//kembalikan tombol export ke kondisi awal
function exportDoReset(){
  const b=el('btn-export-do');
  if(!b) return;
  b.dataset.running = '0';
  b.style.background = '';
  b.style.color = '';
  b.style.border = '';
  b.disabled = false;
  b.innerHTML = 'Export';
}

//batalkan proses export yang sedang berjalan dari modal export biasa
function cancelExport(){
  S.exportCancelling = true;
  fetch('/api/export/cancel',{method:'POST'}).catch(()=>{});
  exportDoReset();
  toast('Info','Export dibatalkan.','warning');
}

//tampilkan notifikasi (toast) di pojok layar dengan judul, pesan, dan tipe (success/danger/warning/info)
function toast(title,msg,type){
  const c=el('toast-ct'), id='t'+Date.now();
  const ico={success:'bi-check-circle-fill',danger:'bi-x-circle-fill',warning:'bi-exclamation-triangle-fill',info:'bi-info-circle-fill'};
  const d=document.createElement('div'); d.id=id; d.className='toast-item '+type;
  d.innerHTML=`<i class="bi ${ico[type]||'bi-info-circle'} ti-icon ${type}"></i><div class="ti-body"><div class="ti-title">${title}</div><div class="ti-msg">${msg.replace(/\n/g,'<br>')}</div></div><button class="ti-x" onclick="document.getElementById('${id}').remove()"><i class="bi bi-x-lg"></i></button>`;
  c.appendChild(d); setTimeout(()=>{ const e=document.getElementById(id); if(e) e.remove(); },5000);
}

//tampilkan notifikasi export berhasil dengan tombol "Buka File" untuk langsung membuka file Excel
function toastWithFolder(title, msg, filepath){
  const c=el('toast-ct'), id='t'+Date.now();
  const safeFilepath = (filepath||'').replace(/\\/g, '/');
  const d=document.createElement('div'); d.id=id; d.className='toast-item success';
  d.innerHTML=`
    <i class="bi bi-check-circle-fill ti-icon success"></i>
    <div class="ti-body">
      <div class="ti-title">${title}</div>
      <div class="ti-msg">${msg}</div>
      <button onclick="fetch('/api/export/open-file?filepath='+encodeURIComponent('${safeFilepath}'))"
        onmouseover="this.style.background='#9e9e9a'"
        onmouseout="this.style.background='#000000'"
        style="margin-top:8px;padding:5px 0;font-size:12px;font-weight:bold;background:#000000;color:white;border:none;border-radius:4px;cursor:pointer;width:100%;display:block;text-align:center;transition:background 0.2s;">
        Buka File
      </button>
    </div>
    <button class="ti-x" onclick="document.getElementById('${id}').remove()"><i class="bi bi-x-lg"></i></button>`;
  c.appendChild(d);
  setTimeout(()=>{ const e=document.getElementById(id); if(e) e.remove(); }, 8000);
}

//fungsi helper: ambil elemen HTML berdasarkan id (shortcut dari document.getElementById)
const el = id => document.getElementById(id);
//fungsi helper: tampilkan elemen dengan display block
const show = e => { if(typeof e==='string') el(e).style.display='block'; else e.style.display='block'; };
//fungsi helper: tampilkan elemen dengan display flex (untuk elemen yang butuh layout flex)
const showEl = id => el(id).style.display='flex';
//fungsi helper: sembunyikan elemen
const hide = e => { if(typeof e==='string') el(e).style.display='none'; else e.style.display='none'; };
//fungsi helper: trigger download file dari url
function dlFile(url,name){ const a=document.createElement('a'); a.href=url; a.download=name; document.body.appendChild(a); a.click(); a.remove(); }

//komponen dropdown yang bisa dicari (searchable select) - menyimpan data semua dropdown
const _ssData = {};

//isi dropdown dengan daftar opsi yang diberikan
function ssPopulate(wrapId, hiddenId, options){
  _ssData[wrapId] = options;
  _ssRender(wrapId, hiddenId, '');
}

//render ulang daftar opsi dropdown (bisa difilter dengan query pencarian)
function _ssRender(wrapId, hiddenId, query){
  const drop = el(wrapId).querySelector('.ss-dropdown');
  const cur = el(hiddenId).value;
  const items = (_ssData[wrapId]||[]).filter(o=>
    !query || o.label.toLowerCase().includes(query.toLowerCase())
  );
  if(!items.length){ drop.innerHTML='<div class="ss-empty">Tidak ditemukan</div>'; return; }
  drop.innerHTML = items.map(o=>`
    <div class="ss-option${o.disabled?' disabled':''}${o.value===cur?' selected':''}"
      data-val="${o.value}" data-wrap="${wrapId}" data-hid="${hiddenId}"
      onclick="ssSelect(this)">${o.label}</div>`).join('');
}

//tangani saat user memilih salah satu opsi dari dropdown
function ssSelect(el_opt){
  if(el_opt.classList.contains('disabled')) return;
  const wrapId=el_opt.dataset.wrap, hiddenId=el_opt.dataset.hid, val=el_opt.dataset.val;
  const data=(_ssData[wrapId]||[]).find(o=>o.value===val);
  el(hiddenId).value = val;
  const wrap=el(wrapId);
  wrap.querySelector('input').value = data?data.label:'';
  wrap.classList.remove('open');
  _ssRender(wrapId, hiddenId, '');

  //jika user memilih label di modal setting, otomatis isi qty plan tersimpan untuk label itu
  if(wrapId === 'ss-label-wrap' && val){
    const stored = loadQtyForLabel(val);
    el('s-qty-plan').value = stored > 0 ? stored : 0;
  }
}

//buka atau tutup dropdown saat tombol diklik
function ssToggle(wrapId){
  const wrap=el(wrapId);
  const isOpen=wrap.classList.contains('open');
  //tutup semua dropdown lain yang mungkin sedang terbuka
  document.querySelectorAll('.ss-wrap.open').forEach(w=>w.classList.remove('open'));
  if(!isOpen){ wrap.classList.add('open'); wrap.querySelector('input').focus(); }
}

//filter opsi dropdown berdasarkan teks yang diketik user
function ssFilter(wrapId, hiddenId){
  const q=el(wrapId).querySelector('input').value;
  _ssRender(wrapId, hiddenId, q);
  if(!el(wrapId).classList.contains('open')) el(wrapId).classList.add('open');
}

//navigasi keyboard pada dropdown: ArrowDown, ArrowUp, Enter, Escape
function ssKeyNav(e, wrapId, hiddenId){
  const wrap = el(wrapId);
  const isOpen = wrap.classList.contains('open');

  //buka dropdown dulu jika belum terbuka saat arrow ditekan
  if((e.key==='ArrowDown'||e.key==='ArrowUp') && !isOpen){
    wrap.classList.add('open');
    e.preventDefault();
    return;
  }

  if(e.key==='Escape'){
    wrap.classList.remove('open');
    wrap.querySelector('input').blur();
    return;
  }

  if(!isOpen) return;

  const opts = [...wrap.querySelectorAll('.ss-option:not(.disabled)')];
  if(!opts.length) return;

  //cari index opsi yang sedang di-highlight (class ss-hover)
  const curIdx = opts.findIndex(o=>o.classList.contains('ss-hover'));

  if(e.key==='ArrowDown'){
    e.preventDefault();
    const nextIdx = curIdx < opts.length-1 ? curIdx+1 : 0;
    _ssSetHover(opts, nextIdx);
  } else if(e.key==='ArrowUp'){
    e.preventDefault();
    const prevIdx = curIdx > 0 ? curIdx-1 : opts.length-1;
    _ssSetHover(opts, prevIdx);
  } else if(e.key==='Enter'){
    e.preventDefault();
    const hovered = wrap.querySelector('.ss-option.ss-hover');
    if(hovered) ssSelect(hovered);
  }
}

//set highlight (hover) pada opsi ke-idx dan scroll agar terlihat
function _ssSetHover(opts, idx){
  opts.forEach(o=>o.classList.remove('ss-hover'));
  opts[idx].classList.add('ss-hover');
  opts[idx].scrollIntoView({block:'nearest'});
}

//set nilai dropdown secara programatik (dari kode, bukan dari klik user)
function ssSetValue(wrapId, hiddenId, val){
  const data=(_ssData[wrapId]||[]).find(o=>o.value===val);
  el(hiddenId).value = data?data.value:'';
  const inp=el(wrapId).querySelector('input');
  if(inp) inp.value = data?data.label:'';
  _ssRender(wrapId, hiddenId, '');
}

//tutup semua dropdown jika user klik di luar area dropdown
document.addEventListener('click', e=>{
  if(!e.target.closest('.ss-wrap')){
    document.querySelectorAll('.ss-wrap.open').forEach(w=>{
      w.classList.remove('open');
    });
  }
});

//buka panel daftar file Excel internal (widget viewer)
function openFileFolder(){
  openExcelPanel();
}

//alias agar tidak error jika dipanggil dari tempat lain
function openExcelList()   { openExcelPanel(); }
function openExcelFolder() { openExcelPanel(); }

//buka folder file_excel langsung di Windows Explorer via backend
function openExcelFolderDirect() {
  fetch('/api/export/open-folder')
    .then(r => r.json())
    .then(d => { if (!d.ok) toast('Gagal', 'Tidak dapat membuka folder.', 'danger'); })
    .catch(() => toast('Error', 'Gagal menghubungi server.', 'danger'));
}

//buka file Excel dengan membuka aplikasi via backend
function openExcelFile(filepath){
  fetch('/api/export/open-file?filepath=' + encodeURIComponent(filepath))
    .then(r=>r.json())
    .then(d=>{ if(!d.ok) toast('Gagal','File tidak ditemukan.','danger'); })
    .catch(()=>{ toast('Error','Gagal membuka file.','danger'); });
}

//buka folder file_excel & highlight/select file yang dipilih di Windows Explorer
function openExcelFolderSelect(filepath){
  fetch('/api/export/open-folder?filepath=' + encodeURIComponent(filepath))
    .then(r=>r.json())
    .then(d=>{ if(!d.ok) toast('Gagal','Tidak dapat membuka folder.','danger'); })
    .catch(()=>{ toast('Error','Gagal menghubungi server.','danger'); });
}

// ─── PANEL FILE EXCEL (internal viewer) ───────────────────────────────────────

let _excelPanelOpen = false;

function openExcelPanel() {
  if (_excelPanelOpen) { closeExcelPanel(); return; }
  _excelPanelOpen = true;

  // buat overlay panel
  const overlay = document.createElement('div');
  overlay.id = 'excel-panel-overlay';
  overlay.onclick = (e) => { if(e.target === overlay) closeExcelPanel(); };

  const panel = document.createElement('div');
  panel.id = 'excel-panel';
  panel.innerHTML = `
    <div class="xp-header">
      <div class="xp-title">
        <i class="bi bi-file-earmark-spreadsheet-fill xp-icon"></i>
        <span>File Export Excel</span>
      </div>
      <button class="xp-close" onclick="closeExcelPanel()" title="Tutup"><i class="bi bi-x-lg"></i></button>
    </div>
    <div class="xp-search-row">
      <i class="bi bi-search xp-search-icon"></i>
      <input type="text" id="xp-search" placeholder="Cari nama file…" oninput="filterExcelPanel(this.value)">
    </div>
    <div id="xp-list" class="xp-list">
      <div class="xp-loading"><i class="bi bi-arrow-clockwise xp-spin"></i> Memuat daftar file…</div>
    </div>
    <div class="xp-footer">
      <span id="xp-count" class="xp-count"></span>
      <button class="xp-refresh-btn" onclick="loadExcelPanelFiles()" title="Refresh"><i class="bi bi-arrow-clockwise"></i> Refresh</button>
    </div>
  `;

  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  requestAnimationFrame(() => {
    overlay.classList.add('xp-show');
    panel.classList.add('xp-show');
  });

  loadExcelPanelFiles();
}

function closeExcelPanel() {
  _excelPanelOpen = false;
  const overlay = document.getElementById('excel-panel-overlay');
  if (!overlay) return;
  overlay.classList.remove('xp-show');
  setTimeout(() => overlay.remove(), 220);
}

let _allExcelFiles = [];

async function loadExcelPanelFiles() {
  const list = document.getElementById('xp-list');
  if (!list) return;
  list.innerHTML = '<div class="xp-loading"><i class="bi bi-arrow-clockwise xp-spin"></i> Memuat…</div>';
  try {
    const d = await (await fetch('/api/export/list')).json();
    _allExcelFiles = (d.files || []).sort((a, b) => {
      // Urutkan berdasarkan mtime descending (terbaru di atas)
      const ta = a.mtime ? new Date(a.mtime.replace(' ', 'T')) : new Date(0);
      const tb = b.mtime ? new Date(b.mtime.replace(' ', 'T')) : new Date(0);
      return tb - ta;
    });
    renderExcelPanelFiles(_allExcelFiles);
  } catch(e) {
    list.innerHTML = '<div class="xp-empty"><i class="bi bi-exclamation-circle xp-empty-icon"></i>Gagal memuat daftar file.</div>';
  }
}

function renderExcelPanelFiles(files) {
  const list = document.getElementById('xp-list');
  const countEl = document.getElementById('xp-count');
  if (!list) return;

  if (countEl) countEl.textContent = `${files.length} file`;

  if (!files.length) {
    list.innerHTML = '<div class="xp-empty"><i class="bi bi-inbox xp-empty-icon"></i>Belum ada file export tersedia.</div>';
    return;
  }

  list.innerHTML = '';
  files.forEach(f => {
    const item = document.createElement('div');
    item.className = 'xp-item';

    // ambil tanggal & jam dari mtime (file system) yang dikirim server
    let dateDisplay = '';
    if (f.mtime) {
      const [datePart, timePart] = f.mtime.split(' ');
      if (datePart) {
        const [yr, mo, dy] = datePart.split('-');
        dateDisplay = `${dy}/${mo}/${yr}`;
        if (timePart) dateDisplay += `  ${timePart}`;
      }
    }



    // Gunakan data-filepath attribute — hindari escaping backslash Windows di onclick string
    item.innerHTML = `
      <div class="xp-item-icon"><i class="bi bi-file-earmark-excel-fill"></i></div>
      <div class="xp-item-info">
        <div class="xp-item-name" title="${f.name}">${f.name}</div>
        <div class="xp-item-meta">
          <span class="xp-item-date"><i class="bi bi-clock"></i> ${dateDisplay}</span>

        </div>
      </div>
      <div class="xp-item-actions">
        <button class="xp-btn-open"   data-filepath title="Buka File">
          <i class="bi bi-box-arrow-up-right"></i> Buka
        </button>
        <button class="xp-btn-folder" data-filepath title="Buka Folder & Highlight File">
          <i class="bi bi-folder2-open"></i>
        </button>
        <button class="xp-btn-delete" data-filepath title="Hapus File">
          <i class="bi bi-trash3"></i>
        </button>
      </div>
    `;
    // Set filepath via property (aman dari escaping apapun)
    const btns = item.querySelectorAll('[data-filepath]');
    btns.forEach(btn => btn.dataset.filepath = f.filepath);

    item.querySelector('.xp-btn-open').addEventListener('click', function(){
      openExcelFile(this.dataset.filepath);
      toast('Membuka','Membuka file Excel…','info');
    });
    item.querySelector('.xp-btn-folder').addEventListener('click', function(){
      openExcelFolderSelect(this.dataset.filepath);
    });
    item.querySelector('.xp-btn-delete').addEventListener('click', function(){
      deleteExcelFile(this.dataset.filepath, this);
    });

    list.appendChild(item);
  });
}

function filterExcelPanel(query) {
  const q = query.trim().toLowerCase();
  if (!q) {
    renderExcelPanelFiles(_allExcelFiles);
  } else {
    renderExcelPanelFiles(_allExcelFiles.filter(f => f.name.toLowerCase().includes(q)));
  }
}

async function deleteExcelFile(filepath, btnEl) {
  const filename = filepath.split(/[\\\/]/).pop();
  const confirmed = await showConfirm(
    `Apakah kamu yakin ingin menghapus file ini?<br><strong>${filename}</strong><br><span style="font-size:11px;color:var(--text-3)">Tindakan ini tidak dapat dibatalkan.</span>`,
    { title: 'Hapus File Excel', icon: 'bi-file-earmark-x-fill', okLabel: 'Hapus' }
  );
  if (!confirmed) return;
  
  try {
    const r = await fetch('/api/export/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ filepath })
    });
    const d = await r.json();
    if (d.ok) {
      toast('Berhasil', 'File berhasil dihapus.', 'success');
      loadExcelPanelFiles();
    } else {
      toast('Gagal', d.msg || 'Gagal menghapus file.', 'danger');
    }
  } catch(e) {
    toast('Error', 'Gagal menghubungi server.', 'danger');
  }
}

// tutup panel jika tekan Escape
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && _excelPanelOpen) closeExcelPanel();
});

//history scan
const HIST_FONT = "'Montserrat',sans-serif";
let histSelectedDate  = null;
let histSelectedLabel = null;
let histSelectedShift = null;
let allHistDates      = []; //simpan semua tanggal untuk filter search

//buka modal history dan muat daftar tanggal
async function openHistory() {
  histSelectedDate  = null;
  histSelectedLabel = null;
  histSelectedShift = null;
  allHistDates      = [];
  const modal = new bootstrap.Modal(el('mHistory'));
  modal.show();
  resetHistBreadcrumb();
  resetHistShiftCol();
  resetHistLabelCol();
  resetHistScanCol();
  //reset search bar
  const searchEl = el('hist-date-search');
  if (searchEl) searchEl.value = '';
  const searchWrap = el('hist-search-wrap');
  if (searchWrap) searchWrap.style.display = 'none';
  await loadHistoryDates();
}

//breadcrumb di atas daftar history: reset, set tanggal, set shift, set label
function resetHistBreadcrumb() {
  el('hist-bc-sep1').style.display  = 'none';
  el('hist-bc-date').style.display  = 'none';
  el('hist-bc-sep2').style.display  = 'none';
  el('hist-bc-shift').style.display = 'none';
  el('hist-bc-sep3').style.display  = 'none';
  el('hist-bc-label').style.display = 'none';
}
function setHistBreadcrumbDate(dateStr) {
  el('hist-bc-sep1').style.display  = 'inline';
  el('hist-bc-date').textContent    = formatHistDate(dateStr);
  el('hist-bc-date').style.display  = 'inline';
  el('hist-bc-sep2').style.display  = 'none';
  el('hist-bc-shift').style.display = 'none';
  el('hist-bc-sep3').style.display  = 'none';
  el('hist-bc-label').style.display = 'none';
}
function setHistBreadcrumbShift(shiftNum) {
  el('hist-bc-sep2').style.display  = 'inline';
  el('hist-bc-shift').textContent   = shiftNum ? `Shift ${shiftNum}` : 'Semua Shift';
  el('hist-bc-shift').style.display = 'inline';
  el('hist-bc-sep3').style.display  = 'none';
  el('hist-bc-label').style.display = 'none';
}
function setHistBreadcrumbLabel(label) {
  el('hist-bc-sep3').style.display  = 'inline';
  el('hist-bc-label').textContent   = label;
  el('hist-bc-label').style.display = 'inline';
}

//kolom 1: daftar tanggal history
async function loadHistoryDates() {
  const list = el('hist-date-list');
  list.innerHTML = `<div style="padding:20px 14px;text-align:center;color:var(--text-3);font-size:12px;font-family:${HIST_FONT}">Memuat…</div>`;
  try {
    const d = await (await fetch('/api/history/dates')).json();
    allHistDates = d.dates || [];
    if (!allHistDates.length) {
      list.innerHTML = `<div style="padding:20px 14px;text-align:center;color:var(--text-3);font-size:12px;font-family:${HIST_FONT}">Belum ada data</div>`;
      return;
    }
    renderHistDateList(allHistDates);
    //auto-pilih tanggal pertama
    list.querySelector('button')?.click();
  } catch(e) {
    list.innerHTML = `<div style="padding:20px 14px;text-align:center;color:var(--text-3);font-size:12px;font-family:${HIST_FONT}">Gagal memuat</div>`;
  }
}

//render daftar tombol tanggal dari array yang diberikan
function renderHistDateList(dates) {
  const list = el('hist-date-list');
  list.innerHTML = '';
  if (!dates.length) {
    list.innerHTML = `<div style="padding:20px 14px;text-align:center;color:var(--text-3);font-size:12px;font-family:${HIST_FONT}">Tidak ditemukan</div>`;
    return;
  }
  dates.forEach(date => {
    const btn = document.createElement('button');
    btn.dataset.date = date;

    const d2 = new Date(date + 'T00:00:00');
    const dayName  = d2.toLocaleDateString('id-ID', {weekday:'long'});
    const dateDisp = d2.toLocaleDateString('id-ID', {day:'2-digit', month:'short', year:'numeric'});

    btn.innerHTML = `
      <div style="font-size:12px;font-weight:700;font-family:${HIST_FONT};color:inherit">${dateDisp}</div>
      <div style="font-size:11px;font-weight:500;font-family:${HIST_FONT};opacity:.65;margin-top:1px">${dayName}</div>
    `;
    btn.style.cssText = `display:block;width:100%;text-align:left;padding:10px 14px;border:none;
      background:transparent;cursor:pointer;border-left:3px solid transparent;
      transition:background .14s,color .14s;color:var(--text-1);`;
    btn.onmouseenter = () => { if (histSelectedDate !== date) btn.style.background = 'var(--hover,#f3f4f6)'; };
    btn.onmouseleave = () => { if (histSelectedDate !== date) btn.style.background = 'transparent'; };
    btn.onclick = () => selectHistDate(date);

    //tandai aktif jika ini tanggal yang sedang dipilih
    if (histSelectedDate === date) {
      btn.style.background      = 'var(--accent-soft,rgba(99,102,241,.1))';
      btn.style.borderLeftColor = 'var(--accent,#6366f1)';
      btn.style.color           = 'var(--accent,#6366f1)';
    }
    list.appendChild(btn);
  });
}

//filter tanggal berdasarkan input search
function filterHistDates(query) {
  const q = query.trim().toLowerCase();
  if (!q) {
    renderHistDateList(allHistDates);
  } else {
    const filtered = allHistDates.filter(date => {
      const d2 = new Date(date + 'T00:00:00');
      const dayName  = d2.toLocaleDateString('id-ID', {weekday:'long'}).toLowerCase();
      const dateDisp = d2.toLocaleDateString('id-ID', {day:'2-digit', month:'short', year:'numeric'}).toLowerCase();
      return date.includes(q) || dateDisp.includes(q) || dayName.includes(q);
    });
    renderHistDateList(filtered);
  }
  //re-aktifkan highlight tanggal yang dipilih jika masih muncul
  if (histSelectedDate) {
    const activeBtn = el('hist-date-list').querySelector(`button[data-date="${histSelectedDate}"]`);
    if (activeBtn) {
      activeBtn.style.background      = 'var(--accent-soft,rgba(99,102,241,.1))';
      activeBtn.style.borderLeftColor = 'var(--accent,#6366f1)';
      activeBtn.style.color           = 'var(--accent,#6366f1)';
    }
  }
}

function selectHistDate(date) {
  histSelectedDate  = date;
  histSelectedLabel = null;
  histSelectedShift = null;
  //highlight tanggal
  document.querySelectorAll('#hist-date-list button').forEach(b => {
    const active = b.dataset.date === date;
    b.style.background      = active ? 'var(--accent-soft,rgba(99,102,241,.1))' : 'transparent';
    b.style.borderLeftColor = active ? 'var(--accent,#6366f1)' : 'transparent';
    b.style.color           = active ? 'var(--accent,#6366f1)' : 'var(--text-1)';
  });
  setHistBreadcrumbDate(date);
  resetHistScanCol();
  resetHistLabelCol();
  loadHistoryShiftPanel(date);
}

//kolom shift: Shift 1 / Shift 2 / Shift 3 clickable buttons
function resetHistShiftCol() {
  const list = el('hist-shift-select-list');
  if (list) list.innerHTML = '<div class="hist-shift-empty">Pilih tanggal</div>';
}

function resetHistLabelCol() {
  el('hist-label-list').innerHTML = `<div id="hist-label-empty" style="padding:24px 14px;text-align:center;color:var(--text-3);font-size:11px;font-family:${HIST_FONT}">Pilih shift</div>`;
}

async function loadHistoryShiftPanel(date) {
  const list = el('hist-shift-select-list');
  if (!list) return;
  list.innerHTML = `<div style="padding:12px 10px;text-align:center;color:var(--text-3);font-size:11px;font-family:${HIST_FONT}">Memuat…</div>`;
  
  try {
    const d = await (await fetch('/api/history/labels/' + date)).json();
    const labels = d.labels || [];
    
    // Kumpulkan shift yang benar-benar ada data
    const shiftsWithData = new Set();
    labels.forEach(lb => { if(lb.shift > 0) shiftsWithData.add(lb.shift); });
    
    list.innerHTML = '';
    
    if (!shiftsWithData.size) {
      list.innerHTML = `<div class="hist-shift-empty">Tidak ada data</div>`;
      return;
    }

    // Hanya tampilkan shift yang ada datanya, urut 1→2→3
    [1, 2, 3].filter(s => shiftsWithData.has(s)).forEach(shiftNum => {
      const btn = document.createElement('button');
      btn.dataset.shift = shiftNum;
      
      const shiftColors = { 1: '#8497d4', 2: '#6a1b9a', 3: '#1b5e20' };
      const color = shiftColors[shiftNum];
      
      btn.innerHTML = `<span style="font-weight:700;font-family:${HIST_FONT};font-size:12px">SHIFT ${shiftNum}</span>`;
      btn.style.cssText = `display:block;width:100%;text-align:left;padding:10px 14px;border:none;
        background:transparent;cursor:pointer;border-left:3px solid transparent;
        transition:background .14s;color:${color};`;
      
      btn.onmouseenter = () => { if(histSelectedShift !== shiftNum) btn.style.background = 'var(--hover,#f3f4f6)'; };
      btn.onmouseleave = () => { if(histSelectedShift !== shiftNum) btn.style.background = 'transparent'; };
      btn.onclick = () => selectHistShift(shiftNum, labels);
      list.appendChild(btn);
    });
    
    // Auto-pilih shift pertama yang ada data
    const firstShift = [1,2,3].find(s => shiftsWithData.has(s));
    if (firstShift) {
      list.querySelector(`button[data-shift="${firstShift}"]`)?.click();
    }
  } catch(e) {
    list.innerHTML = `<div style="padding:12px 10px;text-align:center;color:var(--text-3);font-size:11px;font-family:${HIST_FONT}">Gagal memuat</div>`;
  }
}

function selectHistShift(shiftNum, labelsCache) {
  histSelectedShift = shiftNum;
  histSelectedLabel = null;
  
  // Highlight shift button
  document.querySelectorAll('#hist-shift-select-list button').forEach(b => {
    const active = parseInt(b.dataset.shift) === shiftNum;
    const shiftColors = { 1: '#8497d4', 2: '#6a1b9a', 3: '#1b5e20' };
    const color = shiftColors[parseInt(b.dataset.shift)];
    b.style.background      = active ? 'var(--accent-soft,rgba(99,102,241,.1))' : 'transparent';
    b.style.borderLeftColor = active ? 'var(--accent,#6366f1)' : 'transparent';
    if(parseInt(b.dataset.shift) > 0 && !b.style.opacity.startsWith('0.4')) b.style.color = active ? 'var(--accent,#6366f1)' : color;
  });
  
  setHistBreadcrumbShift(shiftNum);
  resetHistScanCol();
  
  // Filter labels sesuai shift
  const filtered = (labelsCache || []).filter(lb => lb.shift === shiftNum);
  renderHistLabelList(filtered);
}

function renderHistLabelList(labels) {
  const list = el('hist-label-list');
  if (!list) return;
  
  if (!labels.length) {
    list.innerHTML = `<div style="padding:24px 14px;text-align:center;color:var(--text-3);font-size:11px;font-family:${HIST_FONT}">Tidak ada data</div>`;
    return;
  }
  list.innerHTML = '';
  labels.forEach(lb => {
    const btn = document.createElement('button');
    btn.dataset.label = lb.label;
    const allOk  = lb.not_ok === 0;
    const allNok = lb.ok === 0;
    const badgeColor = allOk ? '#16a34a' : allNok ? '#dc2626' : '#f59e0b';
    const badgeBg    = allOk ? '#16a34a18' : allNok ? '#dc262618' : '#f59e0b18';

    btn.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:6px">
        <span style="font-size:12px;font-weight:700;font-family:${HIST_FONT};color:inherit;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${lb.label}">${lb.label}</span>
        <span style="flex-shrink:0;font-size:10px;font-weight:700;font-family:${HIST_FONT};padding:2px 7px;border-radius:8px;background:${badgeBg};color:${badgeColor}">${lb.total}</span>
      </div>
      <div style="display:flex;gap:7px;margin-top:2px">
        <span style="font-size:10px;font-weight:600;font-family:${HIST_FONT};color:#16a34a">√${lb.ok} X${lb.not_ok}</span>
      </div>
    `;
    btn.style.cssText = `display:block;width:100%;text-align:left;padding:8px 12px;border:none;
      background:transparent;cursor:pointer;border-left:3px solid transparent;
      transition:background .14s,color .14s;color:var(--text-1);`;
    btn.onmouseenter = () => { if(histSelectedLabel !== lb.label) btn.style.background = 'var(--hover,#f3f4f6)'; };
    btn.onmouseleave = () => { if(histSelectedLabel !== lb.label) btn.style.background = 'transparent'; };
    btn.onclick = () => selectHistLabel(lb.label);
    list.appendChild(btn);
  });
}

async function loadHistoryLabels(date) {
  const list = el('hist-label-list');
  const shiftList = el('hist-shift-list');
  list.innerHTML = `<div style="padding:20px 14px;text-align:center;color:var(--text-3);font-size:11px;font-family:${HIST_FONT}">Memuat…</div>`;
  if (shiftList) shiftList.innerHTML = '';
  try {
    const d = await (await fetch('/api/history/labels/' + date)).json();
    const labels = d.labels || [];
    if (!labels.length) {
      list.innerHTML = `<div style="padding:24px 14px;text-align:center;color:var(--text-3);font-size:11px;font-family:${HIST_FONT}">Tidak ada data</div>`;
      if (shiftList) shiftList.innerHTML = '';
      return;
    }
    list.innerHTML = '';
    if (shiftList) shiftList.innerHTML = '';
    labels.forEach(lb => {
      const btn = document.createElement('button');
      btn.dataset.label = lb.label;
      const allOk = lb.not_ok === 0;
      const allNok = lb.ok === 0;
      const badgeColor = allOk ? '#16a34a' : allNok ? '#dc2626' : '#f59e0b';
      const badgeBg    = allOk ? '#16a34a18' : allNok ? '#dc262618' : '#f59e0b18';

      btn.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;gap:6px">
          <span style="font-size:13px;font-weight:700;font-family:${HIST_FONT};color:inherit;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${lb.label}">${lb.label}</span>
          <span style="flex-shrink:0;font-size:11px;font-weight:700;font-family:${HIST_FONT};padding:2px 8px;border-radius:8px;background:${badgeBg};color:${badgeColor}">${lb.total}</span>
        </div>
        <div style="display:flex;gap:7px;margin-top:3px">
          <span style="font-size:11px;font-weight:600;font-family:${HIST_FONT};color:#16a34a">OK: ${lb.ok}</span>
          <span style="font-size:11px;font-weight:600;font-family:${HIST_FONT};color:#dc2626">NOT OK: ${lb.not_ok}</span>
        </div>
      `;
      btn.style.cssText = `display:block;width:100%;text-align:left;padding:10px 14px;border:none;
        background:transparent;cursor:pointer;border-left:3px solid transparent;
        transition:background .14s,color .14s;color:var(--text-1);`;
      btn.onmouseenter = () => { if (histSelectedLabel !== lb.label) btn.style.background = 'var(--hover,#f3f4f6)'; };
      btn.onmouseleave = () => { if (histSelectedLabel !== lb.label) btn.style.background = 'transparent'; };
      btn.onclick = () => selectHistLabel(lb.label);
      list.appendChild(btn);

      // Tambahkan item shift yang sejajar dengan baris label
      if (shiftList) {
        const shiftNum = lb.shift || 0;
        const shiftText = shiftNum > 0 ? `SHIFT ${shiftNum}` : '—';
        const shiftClass = shiftNum > 0 ? `shift-${shiftNum}` : '';
        const shiftDiv = document.createElement('div');
        shiftDiv.className = `hist-shift-item ${shiftClass}`;
        shiftDiv.textContent = shiftText;
        shiftList.appendChild(shiftDiv);
      }
    });
  } catch(e) {
    list.innerHTML = `<div style="padding:24px 14px;text-align:center;color:var(--text-3);font-size:11px;font-family:${HIST_FONT}">Gagal memuat</div>`;
  }
}

// Toggle search bar di kolom tanggal
function toggleHistDateSearch() {
  const wrap = el('hist-search-wrap');
  const inp = el('hist-date-search');
  if (!wrap) return;
  const isHidden = getComputedStyle(wrap).display === 'none';
  wrap.style.display = isHidden ? 'block' : 'none';
  if (isHidden && inp) { inp.value = ''; inp.focus(); filterHistDates(''); }
}

function selectHistLabel(label) {
  histSelectedLabel = label;
  //highlight label
  document.querySelectorAll('#hist-label-list button').forEach(b => {
    const active = b.dataset.label === label;
    b.style.background      = active ? 'var(--accent-soft,rgba(99,102,241,.1))' : 'transparent';
    b.style.borderLeftColor = active ? 'var(--accent,#6366f1)' : 'transparent';
    b.style.color           = active ? 'var(--accent,#6366f1)' : 'var(--text-1)';
  });
  setHistBreadcrumbLabel(label);
  //kirim shift yang sedang aktif agar data difilter sesuai shift
  loadHistoryScan(histSelectedDate, label, histSelectedShift);
}

//kolom 4: data scan
function resetHistScanCol() {
  //sembunyikan kolom scan dan kembalikan lebar modal ke mode 3-kolom
  const col = el('hist-scan-col');
  if (col) col.style.display = 'none';
  const dialog = el('hist-modal-dialog');
  if (dialog) dialog.style.maxWidth = '560px'; 
  const tbody = el('hist-scan-tbody');
  if (tbody) tbody.innerHTML = '';
  const planEl = el('hist-scan-plan');
  if (planEl) { planEl.textContent = ''; planEl.style.display = 'none'; }
}                           

//export dari history
const histExportState = { running: false };

async function histExport() {
  //jika sedang berjalan, tombol berfungsi sebagai Batal
  if (histExportState.running) {
    S.exportCancelling = true;
    fetch('/api/export/cancel', {method:'POST'}).catch(()=>{});
    histExportReset();
    toast('Info', 'Export dibatalkan.', 'warning');
    return;
  }
  if (!histSelectedDate || !histSelectedLabel) {
    toast('Perhatian', 'Pilih label terlebih dahulu.', 'warning'); return;
  }

  histExportState.running = true;
  S.exportCancelling = false;

  //update tombol ke state loading — klik lagi = Batal
  const btn = el('hist-export-btn');
  if (btn) histExportSetProgress(0);

  try {
    const planKey   = 'qty_plan:' + histSelectedLabel + ':' + histSelectedDate;
    const planVal   = parseInt(localStorage.getItem(planKey)) || 0;
    const dateParts = histSelectedDate.split('-');
    const monthNames = ['Januari','Februari','Maret','April','Mei','Juni','Juli','Agustus','September','Oktober','November','Desember'];
    const monthName  = monthNames[parseInt(dateParts[1]) - 1] || '';

    const payload = {
      date_range:   'CustomDate',
      preset:       'All',  //baca kedua tabel JIS+DIN agar data history lengkap
      label:        histSelectedLabel,
      month:        monthName,
      year:         parseInt(dateParts[0]),
      start_date:   histSelectedDate,
      end_date:     histSelectedDate,
      qty_plan:     planVal,
      from_history: true,
      shift:        histSelectedShift || 0   //filter data sesuai shift yang dipilih di history
    };

    const r = await (await fetch('/api/export', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    })).json();

    if (!r.ok) {
      histExportReset();
      toast('Error', r.msg || 'Gagal export', 'danger');
    }
    //progress & done ditangani via socket 'export_progress' dan 'export_done'
  } catch(e) {
    histExportReset();
    toast('Error', 'Gagal menghubungi server export.', 'danger');
  }
}

function histExportSetProgress(pct) {
  const btn = el('hist-export-btn');
  if (!btn) return;
  btn.disabled = false;
  btn.style.background = `linear-gradient(90deg,#dc2626 ${pct}%,rgba(231, 25, 25, 0.83) ${pct}%)`;
  btn.innerHTML = `Batal ${pct}%`;
}

function histExportReset() {
  histExportState.running = false;
  const btn = el('hist-export-btn');
  if (btn) {
    btn.disabled = false;
    btn.style.background = 'var(--accent,#6366f1)';
    btn.innerHTML = 'Export';
  }
}

async function loadHistoryScan(date, label, shift) {
  const tbody  = el('hist-scan-tbody');
  const col    = el('hist-scan-col');
  const dialog = el('hist-modal-dialog');

  //lebarkan modal dan tampilkan kolom scan dengan loading state
  col.style.display = 'flex';
  if (dialog) dialog.style.maxWidth = '920px';
  tbody.innerHTML = `<tr><td colspan="4" style="padding:28px;text-align:center;color:var(--text-3);font-family:${HIST_FONT};font-size:12px">Memuat data…</td></tr>`;

  try {
    //sertakan ?shift=N agar server hanya mengembalikan data shift yang dipilih
    const shiftParam = shift ? `?shift=${shift}` : '';
    const d = await (await fetch('/api/history/by_label/' + date + '/' + encodeURIComponent(label) + shiftParam)).json();
    const recs = d.records || [];

    if (!recs.length) {
      col.style.display = 'none';
      if (dialog) dialog.style.maxWidth = '560px';
      return;
    }

    //isi header ringkasan
    el('hist-scan-label-title').textContent = label;
    el('hist-scan-total').textContent       = 'Total: ' + recs.length;
    el('hist-scan-ok').textContent          = 'OK: '     + (d.ok      || 0);
    el('hist-scan-nok').textContent         = 'NOT OK: ' + (d.not_ok  || 0);

    //tampilkan plan dari localStorage (qty_plan:{label}:{date}), fallback ke state server jika label aktif
    const planKey    = `qty_plan:${label}:${date}`;
    const planStored = localStorage.getItem(planKey);
    let   planVal    = planStored ? parseInt(planStored) || 0 : 0;
    //fallback: jika label yang dibuka sama dengan label aktif saat ini, pakai qty_plan dari S
    if (planVal <= 0 && label === S.label && S.qty_plan > 0) planVal = S.qty_plan;
    const planEl = el('hist-scan-plan');
    if (planEl) {
      planEl.textContent   = planVal > 0 ? 'Plan: ' + planVal : 'Plan: —';
      planEl.style.display = '';
    }

    tbody.innerHTML = '';
    recs.forEach((r, idx) => {
      const tr = document.createElement('tr');
      const stripe = idx % 2 === 1 ? 'var(--bg-stripe,rgba(0,0,0,.02))' : '';
      tr.style.cssText = `cursor:default;transition:background .12s;background:${stripe}`;
      tr.onmouseenter = () => tr.style.background = 'var(--hover,#f3f4f6)';
      tr.onmouseleave = () => tr.style.background = stripe;

      const timeOnly    = r.time ? (r.time.split(' ')[1] || r.time).substring(0,8) : '\u2014';
      const isOk        = r.status === 'OK';
      const statusText  = isOk ? 'OK' : 'NOT OK';
      const hasImg      = !!(r.imgPath && r.imgPath.trim());
      const imgFilename = hasImg ? r.imgPath.split(/[\\\/]/).pop() : '';

      tr.innerHTML = `
        <td style="padding:8px 13px;font-size:12px;font-family:${HIST_FONT};color:var(--text-2);white-space:nowrap;overflow:hidden">${timeOnly}</td>
        <td style="padding:8px 13px;font-size:12px;font-family:${HIST_FONT};overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${r.code||''}">${r.code||'\u2014'}</td>
        <td style="padding:8px 13px;text-align:center;overflow:hidden">
          <span style="font-size:11px;padding:3px 9px;border-radius:8px;font-weight:700;font-family:${HIST_FONT};white-space:nowrap;
            background:${isOk?'#16a34a1a':'#dc26261a'};color:${isOk?'#16a34a':'#dc2626'}">
            ${statusText}
          </span>
        </td>
        <td style="padding:6px 13px;text-align:center;font-size:15px">
          ${hasImg
            ? `<button class="hist-foto-btn" onclick="event.stopPropagation();openHistLightbox('/api/image/${imgFilename}','${r.code}','${r.time}')" title="Lihat foto">
                <i class="bi bi-image-fill"></i>
               </button>`
            : `<i class="bi bi-image" style="opacity:.18"></i>`}
        </td>
      `;

      if (hasImg) {
        tr.ondblclick = () => openHistLightbox('/api/image/' + imgFilename, r.code, r.time);
      }
      tbody.appendChild(tr);
    });

  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="4" style="padding:28px;text-align:center;color:var(--text-3);font-family:${HIST_FONT};font-size:12px">Gagal memuat data</td></tr>`;
  }
}

//lightbox untuk menampilkan foto hasil scan (Data Barang & History)
//zoom-in: klik kiri pada foto atau tombol + di bawah
//zoom-out: klik kanan pada foto, tombol - di bawah, atau scroll ke bawah
//drag : tahan klik kiri lalu geser saat sudah zoom in
//reset : double-click pada foto
//tutup : tombol X, klik area gelap di luar foto, atau Escape
let _lbScale  = 1;
let _lbTx     = 0;    //posisi geser horizontal (translate X)
let _lbTy     = 0;    //posisi geser vertikal   (translate Y)
const LB_STEP = 0.25;
const LB_MAX  = 4.0;
const LB_MIN  = 0.5;

//state drag
let _lbDragging  = false;
let _lbDragStartX = 0;
let _lbDragStartY = 0;
let _lbDragOriginTx = 0;
let _lbDragOriginTy = 0;
let _lbDidDrag    = false;  //apakah mouse benar-benar bergerak saat mousedown (bukan klik biasa)

function _lbApply() {
  const img = el('hist-lightbox-img');
  img.style.transform = `translate(${_lbTx}px, ${_lbTy}px) scale(${_lbScale})`;
  img.style.cursor    = _lbDragging ? 'grabbing' : (_lbScale > 1 ? 'grab' : 'zoom-in');
  const lbl = el('lb-zoom-label');
  if (lbl) lbl.textContent = Math.round(_lbScale * 100) + '%';
}

function lbZoomIn(e) {
  if (e) e.preventDefault();
  _lbScale = Math.min(LB_MAX, parseFloat((_lbScale + LB_STEP).toFixed(2)));
  _lbApply();
}

function lbZoomOut(e) {
  if (e) e.preventDefault();
  _lbScale = Math.max(LB_MIN, parseFloat((_lbScale - LB_STEP).toFixed(2)));
  //jika zoom kembali ke 1 atau kurang, reset posisi geser ke tengah
  if (_lbScale <= 1) { _lbTx = 0; _lbTy = 0; }
  _lbApply();
}

function lbZoomReset() {
  _lbScale = 1; _lbTx = 0; _lbTy = 0;
  _lbApply();
}

function openHistLightbox(imgUrl, code, time) {
  _lbScale = 1; _lbTx = 0; _lbTy = 0;
  _lbApply();
  el('hist-lightbox-img').src = imgUrl;
  el('hist-lightbox-caption').textContent = (code||'') + (time ? '  ·  ' + time : '');
  el('hist-lightbox').style.display = 'flex';
}

function closeHistLightbox() {
  el('hist-lightbox').style.display = 'none';
  el('hist-lightbox-img').src = '';
  lbZoomReset();
}

//drag mousedown pada foto
document.addEventListener('mousedown', function(e) {
  const img = el('hist-lightbox-img');
  if (e.button !== 0 || !img || !img.contains(e.target)) return;
  if (el('hist-lightbox').style.display !== 'flex') return;
  _lbDragging     = true;
  _lbDidDrag      = false;
  _lbDragStartX   = e.clientX;
  _lbDragStartY   = e.clientY;
  _lbDragOriginTx = _lbTx;
  _lbDragOriginTy = _lbTy;
  e.preventDefault();
});

//drag mousemove untuk geser foto saat sudah zoom in
document.addEventListener('mousemove', function(e) {
  if (!_lbDragging) return;
  const dx = e.clientX - _lbDragStartX;
  const dy = e.clientY - _lbDragStartY;
  //anggap drag jika bergerak lebih dari 4px
  if (Math.abs(dx) > 4 || Math.abs(dy) > 4) _lbDidDrag = true;
  if (_lbScale > 1) {
    _lbTx = _lbDragOriginTx + dx;
    _lbTy = _lbDragOriginTy + dy;
    _lbApply();
  }
});

//drag: mouseup jika tidak bergerak = zoom in
document.addEventListener('mouseup', function(e) {
  if (!_lbDragging) return;
  _lbDragging = false;
  if (!_lbDidDrag && _lbScale < LB_MAX) {
    //klik biasa (bukan drag) -> zoom in
    lbZoomIn(null);
  }
  const img = el('hist-lightbox-img');
  if (img) img.style.cursor = _lbScale > 1 ? 'grab' : 'zoom-in';
});

//scroll mouse untuk zoom saat lightbox terbuka
document.addEventListener('wheel', function(e) {
  if (el('hist-lightbox').style.display === 'flex') {
    e.preventDefault();
    if (e.deltaY < 0) lbZoomIn(null);
    else              lbZoomOut(null);
  }
}, { passive: false });

//tombol Escape untuk tutup lightbox
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape' && el('hist-lightbox').style.display === 'flex') {
    closeHistLightbox();
  }
});

//helper format tanggal
function formatHistDate(dateStr) {
  try {
    const d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('id-ID', {day:'2-digit', month:'short', year:'numeric'});
  } catch(e) { return dateStr; }
}


//jalankan inisialisasi halaman saat semua kode selesai dimuat
init();
//filter baris tabel berdasarkan query pencarian (waktu, label, atau status)
function filterTable(query){
  const q = query.trim().toLowerCase();
  const clearBtn = el('table-search-clear');
  if(clearBtn) clearBtn.style.display = q ? 'block' : 'none';

  const rows = document.querySelectorAll('#code-tbody tr');
  let visibleCount = 0;
  rows.forEach(tr => {
    if(tr.querySelector('td[colspan]')){ tr.style.display = ''; return; }
    const text = tr.innerText.toLowerCase();
    const match = !q || text.includes(q);
    tr.style.display = match ? '' : 'none';
    if(match) visibleCount++;
  });

  const existingEmpty = document.getElementById('search-empty-row');
  if(existingEmpty) existingEmpty.remove();
  if(q && visibleCount === 0 && S.records.length > 0){
    const tbody = el('code-tbody');
    const emptyTr = document.createElement('tr');
    emptyTr.id = 'search-empty-row';
    emptyTr.innerHTML = `<td colspan="3" style="padding:20px;text-align:center;color:var(--text-3);font-size:12px">Tidak ada hasil untuk "<strong>${query}</strong>"</td>`;
    tbody.appendChild(emptyTr);
  }
}

//bersihkan input pencarian dan tampilkan semua baris kembali
function clearTableSearch(){
  const q = el('table-search');
  if(q){ q.value = ''; filterTable(''); q.focus(); }
}


//sistem shift otomatis berdasarkan jam kerja gs-battery
const SHIFT_INFO = {
  1: { label: 'Shift 1', time: '07:00–15:59', cls: 'shift-1' },
  2: { label: 'Shift 2', time: '16:00–23:59', cls: 'shift-2' },
  3: { label: 'Shift 3', time: '00:00–06:59', cls: 'shift-3' },
};

//hitung shift aktif berdasarkan jam lokal browser
function autoDetectShift() {
  const h = new Date().getHours();
  if (h >= 7 && h < 16)   return 1;  // 07:00–15:59
  if (h >= 16 && h < 24)  return 2;  // 16:00–23:59
  return 3; // 00:00–06:59
}

//perbarui tampilan badge shift di topbar
function updateShiftBadge(shift) {
  const badge = el('shift-badge');
  if (!badge) return;
  const info = SHIFT_INFO[shift];
  badge.textContent = info ? info.label : 'Shift —';
  badge.className   = `shift-badge-inline${info ? ' ' + info.cls : ''}`;
}

//cek setiap 30 detik — fallback jika SocketIO tidak terhubung
let _lastShift = autoDetectShift();
function _pollShiftChange() {
  const current = autoDetectShift();
  if (current !== _lastShift) {
    _lastShift = current;
    S.shift = current;
    updateShiftBadge(current);
    refreshData().catch(() => {});
  }
}
setInterval(_pollShiftChange, 30000);

// refresh otomatis saat server mengirim event pergantian shift
io_socket.on('shift_change', d => {
  _lastShift = d.next_shift;
  S.shift    = d.next_shift;
  updateShiftBadge(d.next_shift);
  refreshData().catch(() => {});
});

//banner auto-export
function showAutoExportBanner(ok, msg) {
  const banner = el('auto-export-banner');
  const msgEl  = el('aeb-msg');
  const icon   = el('aeb-icon');
  const title  = el('aeb-title');
  const wrap   = el('aeb-progress-wrap');
  const bar    = el('aeb-progress-bar');
  const label  = el('aeb-progress-label');
  if (!banner) return;
  // sembunyikan progress bar saat selesai/gagal
  if(wrap)  wrap.classList.remove('show');
  if(bar)   bar.style.width = '0%';
  if(label) label.textContent = '0%';
  if (ok) {
    icon.className  = 'bi bi-file-earmark-check-fill aeb-icon';
    icon.style.color = '#22c55e';
    title.textContent = 'Auto-Export Selesai';
  } else {
    icon.className  = 'bi bi-exclamation-triangle-fill aeb-icon';
    icon.style.color = '#ef4444';
    title.textContent = 'Auto-Export';
  }
  msgEl.textContent = msg || '';
  banner.classList.add('show');
  // tutup otomatis setelah 10 detik
  setTimeout(() => banner.classList.remove('show'), 10000);
}

function closeAutoExportBanner() {
  el('auto-export-banner')?.classList.remove('show');
}

//terima notifikasi server saat file Excel expired dan auto-dihapus
io_socket.on('excel_files_changed', d => {
  if (d.reason === 'expired' && d.deleted && d.deleted.length > 0) {
    const jumlah = d.deleted.length;
    const label  = jumlah === 1 ? `File "${d.deleted[0]}"` : `${jumlah} file Excel`;
    toast(
      'File Excel Dihapus Otomatis',
      `${label} telah dihapus karena masa berlakunya sudah habis.`,
      'warning'
    );
    // refresh panel file excel jika sedang terbuka
    if (typeof _excelPanelOpen !== 'undefined' && _excelPanelOpen) {
      loadExcelPanelFiles();
    }
  }
});

//terima notifikasi auto-export dari server via SocketIO
io_socket.on('auto_export_done', d => {
  const shiftInfo  = SHIFT_INFO[d.shift] || {};
  const shiftLabel = shiftInfo.label || 'Shift';
  const hasErrors  = d.errors && d.errors.length > 0;

  if (d.ok) {
    //berhasil — tapi mungkin ada sebagian label yang gagal
    let msg = `${shiftLabel} — File: ${d.filename}`;
    if (hasErrors) {
      msg += `\n⚠ ${d.errors.length} label gagal: ${d.errors.slice(0, 3).join(', ')}${d.errors.length > 3 ? ' ...' : ''}`;
    }
    showAutoExportBanner(true, msg);
    toast('Auto-Export', `${shiftLabel} berhasil diekspor.${hasErrors ? ` (${d.errors.length} label gagal)` : ''}`, hasErrors ? 'warning' : 'success');
  } else if (d.no_data) {
    showAutoExportBanner(false, `${shiftLabel} — Tidak ada data untuk diekspor.`);
  } else {
    //gagal semua — tampilkan pesan error dan label yang gagal jika ada
    let msg = `${shiftLabel} — ${d.msg || 'Gagal export.'}`;
    if (hasErrors) {
      msg += `\nLabel gagal: ${d.errors.slice(0, 3).join(', ')}${d.errors.length > 3 ? ` (+${d.errors.length - 3})` : ''}`;
    }
    showAutoExportBanner(false, msg);
    toast('Auto-Export Error', d.msg || 'Gagal', 'danger');
  }
});