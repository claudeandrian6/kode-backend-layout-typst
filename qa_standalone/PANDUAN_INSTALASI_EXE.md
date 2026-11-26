# Panduan Instalasi, Pengemasan `.exe`, dan Pemakaian QA Layout Jurnal

Aplikasi ini adalah alat pemeriksa tata letak PDF mandiri untuk tim desain **Scribus** dan **PageMaker** agar hasil PDF sesuai aturan ketat jurnal (IMRECS A4 2 kolom: BSSCD / PEDREV / RIETM), tanpa memerlukan koneksi server maupun sistem Typst.

---

## 1. Menjalankan Langsung dari Python (Windows / macOS / Linux)

Pastikan Python 3.10+ sudah terpasang, lalu jalankan dari dalam folder `qa_standalone/`:

```bash
pip install -r requirements.txt
python app_gui.py
```

Atau langsung membuka sebuah file PDF:

```bash
python app_gui.py "C:\jalur\ke\artikel.pdf"
```

Mode terminal (CLI) untuk otomasi atau ekspor cepat:

```bash
python qa_cli.py "artikel.pdf" --html "laporan_qa.html" --json "laporan_qa.json"
```

---

## 2. Mengemas Menjadi File `.exe` Portabel (Windows)

Setelah menyalin folder `qa_standalone` ke komputer Windows:

### Cara Otomatis (1 Klik)
1. Pastikan Python 3.10+ (64-bit) sudah terinstal dengan opsi **"Add Python to PATH"** dicentang.
2. Klik ganda file **`build_exe.bat`**.
3. Tunggu hingga proses instalasi paket (`pymupdf`, `Pillow`, `tkinterdnd2`, `pyinstaller`) selesai.
4. Hasil file mandiri berada di:
   **`dist\QA_Layout_Jurnal.exe`**

### Cara Manual (Command Prompt / PowerShell)
```bat
cd path\ke\qa_standalone
python -m venv .venv_build
.venv_build\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm qa_jurnal.spec
```

---

## 3. Distribusi ke Tim Layout Editor (Scribus / PageMaker)

- Anda cukup membagikan **1 file `QA_Layout_Jurnal.exe`** saja ke tim desain.
- Tim desain **tidak perlu menginstal Python** ataupun pustaka tambahan.
- Untuk memeriksa PDF:
  1. Buka `QA_Layout_Jurnal.exe`.
  2. Klik tombol **"Pilih File PDF..."**, **tarik-dan-lepas (drag-and-drop)** file PDF ke area pratinjau di sebelah kiri, atau seret file PDF langsung ke ikon `QA_Layout_Jurnal.exe` di Desktop.
  3. Klik baris pelanggaran di daftar sebelah kanan untuk langsung melompat ke halaman terkait dan melihat kotak sorotan serta saran perbaikan.
  4. Klik **"Ekspor Laporan HTML"** untuk menyimpan laporan visual lengkap (berisi gambar halaman beranotasi) yang bisa dibuka di browser atau dikirim ke tim revisi.

---

## 4. Arti Kode Warna & Jenis Pelanggaran

- **Garis Putus-putus Cyan (Panduan)**: Batas margin halaman (`Kiri 76.5 pt`, `Kanan 518.7 pt`, `Atas 102.4 pt`, `Bawah 762.9 pt`) dan batas **Gutter Tengah** (`290.1 pt – 305.1 pt`, lebar celah `15 pt`).
- **Merah (`FATAL` / `PELANGGARAN`)**:
  - **Keseimbangan Kolom**: Kolom kanan lebih panjang dari kolom kiri, kolom kiri menggantung di halaman non-akhir, kolom kosong di samping kolom berisi objek, ruang kosong berlebih di bawah gambar, atau kolom kanan terlalu pendek.
  - **Orphan Heading**: Judul sub-bab berada di dasar kolom dengan jumlah baris teks di bawahnya kurang dari batas minimal (`2` atau `3` baris).
  - **Widow / Orphan**: 1 baris terakhir paragraf terdampar sendirian di puncak kolom (*widow*) atau 1 baris awal paragraf terdampar sendirian di dasar kolom (*orphan*).
  - **Format Rumus (Equation)**: Rumus tidak miring (*italic*), menjorok masuk (*indent*), atau kekurangan jarak spasi `11 pt` di atas/bawah rumus.
  - **Jarak Sitasi & Header**: Jarak blok `"To cite this article"` ke sub-bab pertama (`INTRODUCTION`) tidak sama dengan `22 pt` (2 baris).
  - **Objek Berurutan / Bertumpuk**: Ditemukan 3 atau lebih objek (gambar/tabel) berturut-turut tanpa diselingi teks narasi.
  - **Objek / Teks Menabrak Margin atau Kolom**: Gambar, grafik, tabel melanggar margin ketat (`2.5 pt`) / gutter (`3.0 pt`), atau teks keluar melampaui toleransi optik margin/footer (area *running footer* jurnal dikecualikan otomatis).
  - **Pita Teks Terjepit**: Hanya tersisa `< 3` baris teks terjepit di antara dua objek besar (tabel/gambar).
- **Oranye (`PERINGATAN`)**:
  - **Grid 11 pt**: Baris teks narasi bergeser dari fase garis dasar `11 pt` antar kolom (teks *running footer* tidak diperiksa).
- **Biru Terang (Sorotan Aktif)**: Pelanggaran yang sedang dipilih di daftar sebelah kanan.
