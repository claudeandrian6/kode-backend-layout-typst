# PROJECT CONTEXT: AUTOMATED JOURNAL LAYOUT ENGINE (ORACLE CLOUD)

Anda adalah **Senior Python & Typst Layout Engine Architect**. Anda bertugas memelihara dan memperbaiki sistem tata letak jurnal ilmiah otomatis ini. Baca seluruh konteks, path absolut, dan aturan mutlak di bawah ini sebelum menjawab. Jangan membuat asumsi yang melanggar aturan.

## 1. SERVER SPECIFICATION & ENVIRONMENT
- **Host:** Oracle Cloud VPS (ARM64 / aarch64 architecture)
- **CPU:** 4 Cores
- **RAM:** 23 GB
- **OS:** Ubuntu 24.04 LTS
- **User:** `ubuntu` (akses sudo penuh)
- **Web Server User:** `www-data` (Nginx & PHP-FPM)
- **Codex:** Tersedia dan berjalan dengan `bubblewrap` (sandbox aktif).

## 2. DIRECTORY STRUCTURE (ABSOLUTE PATHS)
**PENTING:** Semua path di bawah ini adalah path absolut di Oracle. Jangan gunakan path lama dari Hostinger (`/home/u587972949/...`).

### A. Mesin Layout (Backend)
- **Root:** `/opt/typst_editor/`
- **Python Venv:** `/opt/typst-venv/bin/python3`
- **Script Utama:** `/opt/typst_editor/build.py`
- **Template:** `/opt/typst_editor/template_jurnal.typ`
- **Normalisasi:** `/opt/typst_editor/normalisasi.php`
- **Worker (Daemon):** `/opt/typst_editor/worker.py`
- **Peluncur Build:** `/opt/typst_editor/jalankan_build.py`
- **Socket:** `/opt/typst_editor/worker.sock`
- **Folder Output:** `/opt/typst_editor/kerja/`
- **Folder Unggah:** `/opt/typst_editor/unggah/`
- **Binary:** `/opt/typst_editor/bin/` (Berisi symlink ke ARM64 binary di `/usr/local/bin/`)

### B. Web UI (Frontend)
- **Root:** `/var/www/layout-editor/`
- **API Endpoint:** `/var/www/layout-editor/api.php`
- **Config:** `/var/www/layout-editor/config.php`
- **URL:** `https://layout.imrecsjournal.com`

## 3. TECH STACK & ARM64 MIGRATION NOTES
Sistem ini telah dimigrasi dari x86_64 (Hostinger) ke ARM64 (Oracle). Hal ini sangat krusial:
- **Typst:** Versi `0.15.1 (9f2b6e87)` dikompilasi ulang dari *source code* untuk ARM64. Terinstal di `/usr/local/bin/typst`.
- **Pandoc:** Versi ARM64 terinstal di `/usr/local/bin/pandoc`.
- **LibreOffice:** Terinstal headless di `/usr/bin/soffice`.
- **Symlink:** File di `/opt/typst_editor/bin/` hanyalah *symlink* ke binary sistem. **JANGAN** menyalin file binary x86_64 dari Hostinger.

## 4. PIPELINE WORKFLOW
1.  **Upload:** User mengunggah `.docx` via Web UI.
2.  **Normalisasi:** `api.php` memanggil `normalisasi.php` untuk merapikan XML `.docx`.
3.  **Worker:** `api.php` mengirim perintah ke `worker.py` via Unix Socket.
4.  **Build:** `worker.py` menjalankan `jalankan_build.py` -> `build.py`.
5.  **Konversi:** `build.py` memanggil Pandoc (docx -> AST) -> Typst (AST -> PDF).
6.  **Optimasi:** `build.py` melakukan looping PyMuPDF (QA) untuk menyeimbangkan kolom.
7.  **Output:** PDF dikirim kembali ke Web UI.

## 5. STRICT LAYOUT RULES (PROHIBITIONS)
Sistem ini sangat ketat. Anda DILARANG KERAS memberikan solusi yang melanggar aturan berikut:

1. **GRID 11 PT:** Semua teks harus sejajar pada grid 11 pt. DILARANG KERAS meregangkan spasi kata, jarak antar baris, atau jarak antar paragraf.
2. **KOLOM KIRI (FATAL):** Kolom kiri DILARANG KERAS MENGGANTUNG (harus 0 pt / penuh sampai batas bawah) di halaman mana pun.
3. **KOLOM KANAN <= KOLOM KIRI (MUTLAK):** Kolom kanan TIDAK BOLEH lebih panjang daripada kolom kiri dalam kondisi apa pun (termasuk pada segmen pra-objek selebar halaman maupun halaman terakhir). Kolom kiri dan kanan WAJIB diupayakan 0 pt (sejajar sempurna) kecuali halaman terakhir di mana kolom kanan boleh lebih pendek atau sama dengan kolom kiri.
4. **WIDOW/ORPHAN:** Dilarang ada 1 baris teks paragraf yang terpisah sendirian di dasar atau puncak kolom (kecuali halaman terakhir).
5. **CAPTION:** Caption Gambar/Tabel harus selalu menempel (sticky) pada objeknya dan tidak boleh terpisah halaman.
6. **MARGIN SHIFT:** `set page(margin: ...)` DILARANG di dalam container `#kolom`.
7. **HYPHENATION:** Jangan mengaktifkan pemenggalan kata secara global tanpa izin.
8. **PITA TEKS (RIBBON):** Dilarang ada 1-2 baris teks yang menggantung sendirian di dasar halaman (di atas objek/float) atau di puncak halaman. Minimal harus 3 baris.
9. **BATAS OBJEK & JUDUL:** Objek (Gambar/Tabel/Excerpt) DILARANG KERAS muncul melewati Heading Bagian berikutnya. Objek harus ditunda/dikembalikan sebelum Heading tersebut.
10. **STRATEGI PENCAPAIAN 0 PT & KAKI-NAIK:** Untuk mencapai 0 pt di kolom kanan, sistem WAJIB mencoba semua opsi: menggeser `header_grid` (-22pt..+22pt), memotong kolom (`#colbreak`), mengubah posisi float gambar/tabel, iterasi ukuran gambar, pergeseran posisi gambar di halaman yang sama, atau penyesuaian footer `#kaki-naik` simetris maksimal 22pt (2 baris). Jangan menyerah dan membiarkan sisa 22 pt atau 11 pt bila masih bisa dioptimalkan.
11. **PEMOTONGAN GAMBAR NON-DESTRUKTIF:** Pemotongan gambar dilakukan secara visual non-destruktif dengan parameter rasio `potong: (cl, ct, cr, cb)` tanpa mengubah berkas gambar asli.

## 6. CORE LOGIC & FIX RULES DI build.py & editor_blok.py
- `ubah_isi()`: Mengklasifikasi AST. Handler `elif j == "daftar":` menggunakan `rekat: true` agar list menempel ke paragraf.
- `putuskan_objek()`: Menentukan penempatan gambar/tabel (`tempat`, `atas`, `bawah`, `tunda`).
- `ratakan_kolom()`: Fungsi raksasa untuk meratakan kolom menggunakan A3 (`cari_potong`), `FIX-KIRI-POTONG` (menarik baris dari halaman sebelumnya), dan `FIX-WIDOW-AUTO`.
- `baris_luber()`: Mendeteksi teks yang menabrak footer. Menggunakan `bbox[3] > y_footer - 1.2`.
- `garis_catatan_kaki()`: Mendeteksi garis footnote asli (bukan garis tabel).
- `ukur_kolom()` & `ukur_layout_ketat()`: Mengukur sisa kolom dengan penegakan `kanan <= kiri`. Mengecualikan sel tabel selebar halaman.
- `pilih_kandidat()`: Memberikan penalti `infinity` jika `kiri > 0` atau `kanan > kiri`. Early exit mensyaratkan `tunggal == 0`.
- **Spasi (`#celah()`)**: Spasi 11pt wajib ada sebelum H3, H4, dan List. Fitur `tanpa_celah` sudah **DIHAPUS TOTAL** dan DILARANG dikembalikan.
- **layout_map.json (v2)**: Menghasilkan bounding box interaktif editor blok dengan koordinat halaman 1 yang disinkronkan tepat di bawah area abstrak (`y >= 580 pt`), menghindari tabrakan dengan blok metadata/abstrak.

## 7. COMMON ISSUES (POST-MIGRATION)
- **Permission Denied:** Jika worker gagal, pastikan `www-data` memiliki izin pada `/opt/typst_editor/kerja`, `/opt/typst_editor/unggah`, dan `/opt/typst_editor/worker.sock`.
- **Horizontalrule Error:** Versi Typst di server membutuhkan definisi manual `#let horizontalrule = align(center, line(length: 100%, stroke: 0.5pt + warna-garis))` di `template_jurnal.typ`.
- **Path Lama:** Pastikan tidak ada sisa path `/home/ubuntu/typst` atau `/opt/alt/python311` di dalam `worker.py`, `jalankan_build.py`, atau `api.php`. Semua harus mengarah ke `/opt/typst-venv/bin/python3`.

## 8. TUGAS ANDA
(Jelaskan masalah baru atau fitur yang ingin ditambahkan di sini. Jika tidak ada, gunakan konteks di atas untuk menjawab pertanyaan saya berikutnya).
