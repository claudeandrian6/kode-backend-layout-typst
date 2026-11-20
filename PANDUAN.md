# Layout Editor — panduan pemasangan (Hostinger)

Editor tata letak berbasis web untuk hasil `build.py` (Typst). Penata letak dapat:

- melihat semua halaman artikel, dengan kotak gambar/tabel dan penanda paragraf (●) yang dapat diklik;
- mengubah **ukuran gambar** (slider) dan **posisi objek** (di posisi naskah / atas halaman / dasar halaman / halaman berikutnya);
- memaksa paragraf **mulai di kolom berikutnya**;
- menaikkan **footer** per halaman;
- **mengedit teks** satu paragraf (sumber Typst);
- melihat **pemeriksaan aturan** (kolom kiri penuh, baris tunggal, pita teks, grid) yang langsung diperbarui setiap perubahan;
- **membatalkan** perubahan (maks. 60 langkah) dan **mengunduh PDF**;
- mengunggah naskah `.docx` baru → tata letak otomatis (`build.py`) berjalan di server, log tampil langsung.

## Susunan di server

```
/home/u587972949/typst_editor/            ← MESIN (di luar public_html, tidak dapat dibuka dari web)
├── build.py, template_jurnal.typ, qa_cepat.py
├── bin/typst   (Typst dev 0.15.1 9f2b6e8, Linux)   ← diunduh pasang.sh
├── bin/pandoc  (Linux statis)                        ← diunduh pasang.sh
├── fonts/      (Times New Roman, Cambria Math, …)    ← diunggah dari komputer Anda
├── jurnal/     (config.json + logo per jurnal)       ← diunggah dari komputer Anda
├── kerja/      (satu folder per artikel)
├── unggah/     (docx yang diunggah + log build)
└── layout_editor_web/  (salinan berkas web untuk pasang.sh)

/home/u587972949/domains/imrecsjournal.com/public_html/tools/layout-editor/   ← TAMPILAN WEB
├── index.php, api.php, app.js, gaya.css, .htaccess, .user.ini
└── config.php  (dibuat pasang.sh; berisi hash kata sandi)
```

Alamat: **https://imrecsjournal.com/tools/layout-editor/** (folder web dapat diubah di `pasang.sh`, variabel `WEB`).

## Langkah pemasangan (dari PowerShell di komputer Anda)

Jalankan dari folder proyek lokal (yang berisi `build.py`, `template_jurnal.typ`, `jurnal\`, `fonts\`, `layout_editor\`).

1. **Siapkan font.** Pastikan folder `fonts\` berisi font yang dipakai jurnal, misalnya dari `C:\Windows\Fonts`:
   `times.ttf`, `timesbd.ttf`, `timesi.ttf`, `timesbi.ttf`, dan `cambria.ttc` (untuk Cambria Math).

   ```powershell
   Copy-Item C:\Windows\Fonts\times*.ttf, C:\Windows\Fonts\cambria.ttc .\fonts\
   ```

2. **Unggah mesin & berkas web.**

   ```powershell
   ssh myserver "mkdir -p ~/typst_editor"
   scp build.py template_jurnal.typ layout_editor\mesin\qa_cepat.py layout_editor\pasang.sh myserver:typst_editor/
   scp -r jurnal fonts myserver:typst_editor/
   scp -r layout_editor\web myserver:typst_editor/layout_editor_web
   ```

3. **Jalankan pemasang** (mengunduh Typst dev & pandoc, memasang pymupdf/pillow, menyalin berkas web,
   meminta kata sandi editor dan membuat `config.php`):

   ```powershell
   ssh -t myserver "bash ~/typst_editor/pasang.sh"
   ```

4. **Periksa `config.php`** di folder web: `SOFFICE_BIN` harus menunjuk ke `AppRun` LibreOffice Anda
   (dipakai untuk chart Word dan gambar EMF). Sesuaikan bila jalurnya berbeda.

5. **Buka** https://imrecsjournal.com/tools/layout-editor/, masuk dengan kata sandi tadi.
   Untuk diagnostik: setelah masuk, buka `…/layout-editor/api.php?aksi=cek` — semua isian harus terisi
   (proc_open `true`, versi Python + pymupdf, `typst 0.15.1 (9f2b6e87)`, pandoc).

## Memperbarui

Setiap kali `build.py`, `template_jurnal.typ`, atau berkas editor berubah, ulangi langkah 2 untuk berkas
yang berubah, lalu salin berkas web:

```powershell
scp build.py template_jurnal.typ layout_editor\mesin\qa_cepat.py myserver:typst_editor/
cd layout_editor\web
scp index.php api.php app.js gaya.css .htaccess .user.ini config.contoh.php myserver:domains/imrecsjournal.com/public_html/tools/layout-editor/
cd ..\..
```

(`config.php` di server tidak tertimpa karena tidak ada di folder `web\` lokal.)

## Memindahkan artikel yang sudah dibangun di komputer lokal

Artikel yang sudah dibangun di komputer Anda dapat langsung disunting di editor: salin foldernya.

```powershell
scp -r kerja\308-2456-1-LE myserver:typst_editor/kerja/
```

## Catatan

- **Keamanan:** semua aksi memerlukan login; cookie sesi `HttpOnly` + `SameSite=Strict`; `config.php`
  ditutup dari web; mesin dan data artikel berada di luar `public_html`.
- **Cache:** `.htaccess` mematikan cache LiteSpeed untuk folder editor, dan API mengirim `Cache-Control: no-store`.
- **Batas server:** build otomatis naskah dengan banyak gambar besar bisa memakan 10–15 menit dan berjalan
  di latar belakang. Bila Hostinger (CloudLinux) menghentikan proses yang terlalu lama, bangun artikel di
  komputer lokal lalu unggah folder `kerja\<nama>` seperti di atas; penyuntingan di editor tetap ringan
  (±2–4 detik per perubahan).
- **Akses dari jaringan yang diblokir firewall Hostinger** berlaku sama seperti untuk situs jurnal
  (blokir per IP publik sumber); gunakan tethering atau minta Hostinger membuka IP tersebut.
- Perubahan disimpan langsung ke `kerja/<nama>/body.typ`; riwayat untuk *Batal* di `kerja/<nama>/_riwayat/`.

- **Pembaruan Backend (Editor Blok v2)**:
  - Pemotongan visual non-destruktif: parameter `crops` dipetakan ke sintaks `#gambar(..., potong: (cl, ct, cr, cb))` tanpa merusak berkas gambar sumber.
  - Pengangkatan footer (`kaki_naik`): dibatasi maksimal 2 baris (22 pt) sesuai grid 11 pt agar tetap harmonis dengan footer standar.
  - Kompilasi paralel: `kompilasi_svg` memproduksi `hasil.pdf` dan seluruh halaman SVG secara paralel demi efisiensi waktu respons editor.
  - Layout map v2 (`layout_map.json`): penanda posisi blok metadata Halaman 1 terkalibrasi presisi dengan batas segmen di bawah area abstrak (`y >= 580 pt`), menghilangkan tumpang tindih seleksi blok.

### ARSITEKTUR AI AGENT (GEMINI ANTIGRAVITY)
- **Agen Utama (Orchestrator)**: Orchestrator dan planner adalah default agent. Bertugas menganalisis logika, membaca konteks, dan menyusun rencana (plan).
- **Sub-Agen (Executor)**: Gemini Flash. Bertugas mengeksekusi kode (edit file, jalankan perintah) berdasarkan instruksi Agen Utama.
- **Aturan**: Agen Utama TIDAK BOLEH mengedit kode secara langsung. Wajib mendelegasikan perubahan ke Sub-Agen.
