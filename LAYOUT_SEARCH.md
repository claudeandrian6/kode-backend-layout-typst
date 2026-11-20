# Pencarian layout pada grid 11 pt

Naskah 549 dievaluasi dari seluruh 24 halaman kondisi awal. Ada delapan pelanggaran
kolom; dua halaman memakai pengecualian footer. Pembanding 23 halaman dikompilasi
ulang memakai pembaca glyph terlihat yang sama.

## Strategi

1. Gunakan parameter layout terdahulu yang cocok dengan SHA-256 input Word dan
   metadata sebagai kandidat awal. Resep hanya menyimpan keputusan objek, pergeseran
   header, dan spesifikasi potong; tidak menyimpan keputusan LULUS.
2. Kompilasi setiap kandidat dengan template produksi. Ukur baseline glyph terlihat,
   grid, fragmen paragraf, overflow, urutan objek, dan pita teks.
3. Jika belum lulus, kembangkan beam lewat empat kelompok operator: header,
   ukuran raster, posisi gambar/tabel, dan titik putus kolom. Round-robin membagi
   anggaran setiap generasi agar satu kelompok tidak menghabiskan antrean.
4. Ketika objek disusun ulang, terapkan kembali spesifikasi potong yang terpilih.
   Operator gabung dapat mencabut potongan yang tidak lagi diperlukan.
5. Deduplicasi berdasarkan hash teks Typst dan parameter header. Spesifikasi
   potong dihitung sewaktu PDF kandidat masih tersedia; parent tidak perlu
   dikompilasi ulang hanya untuk membaca titik potong.
6. Pilih berdasarkan pelanggaran wajib, jumlah masalah/kolom bermasalah, defisit
   baseline, prefix halaman yang sudah penuh, jumlah halaman, dan biaya perubahan.
7. Jalankan QA lengkap lagi pada PDF akhir. Simpan resep hanya sesudah LULUS.
   Input atau metadata yang berubah membatalkan penggunaan resep sebelumnya.
   Pencarian berbatas yang belum menemukan solusi tetap menghasilkan DITAHAN.

Pipeline utama tidak lagi menjalankan pass hardcode halaman 581/595 setelah pencarian.
Kelonggaran kanan berlaku hanya pada halaman paling terakhir, maksimum 200 pt.
Kolom kiri tetap wajib penuh termasuk halaman terakhir.

## Hasil eksplorasi 549

- Delapan kombinasi awal menguji pergeseran header -11/0/11/22 pt dan potongan
  paragraf 31 yang dipertahankan atau digabung.
- Menggabungkan kembali paragraf 31 dan menambah ruang area header 11 pt
  menyisakan satu kolom kiri pendek pada halaman 583.
- Perubahan gambar pertama dari skala 0.686 ke 0.693 (sekitar +1%) menutup
  kekurangan tersebut tanpa menimbulkan masalah di halaman berikutnya.
- Build penuh dari Word kemudian mengulang kandidat terpilih: 23 halaman,
  seluruh 23 kolom kiri penuh, seluruh 22 kolom kanan nonakhir penuh,
  dan sisa kanan halaman terakhir 44 pt.
- Semua footer kembali standar, tanpa colbreak manual pada hasil terpilih.
- Cakupan teks 100%; widow/orphan, pelanggaran grid, dan overflow semuanya nol.

Parameter header-grid mengubah clearance blok header halaman pertama dalam kelipatan
11 pt; font, leading, spasi paragraf, dan spasi kata tidak diubah untuk mengisi kolom.
Resep tersimpan di unggah/549-3461-1-LE.layout.json dan selalu diuji ulang.

### REVISI BOBOT PENCARIAN (PENALTI)
- `kanan_kurang_22pt`: Berikan penalti berat. Sistem harus mencoba varian `header_grid` atau `colbreak` untuk menarik 2 baris dari halaman berikutnya sebelum menerima sisa 22 pt.
- `pita_2_baris`: Penalti fatal (DITAHAN). Paksa objek di bawahnya untuk naik (float ke atas) atau turun (geser).
- `objek_melewati_judul`: Penalti fatal (DITAHAN). Batasi `target` float agar tidak melewati indeks paragraf Heading.

### Algoritma Iteratif Kesejajaran Kolom & Fitur Editor v2
- **Pencarian Lanjutan Sisa 11 pt ke 0 pt**: Pencarian layout tidak langsung berhenti saat menemukan solusi sisa 11 pt bila masih ada anggaran iterasi dan belum mencapai sisa 0 pt sempurna.
- **Kompensasi Header-Grid Negatif (-11 pt & -22 pt)**: Penambahan kompensasi spasi judul dan pembatas pada template Typst memungkinkan `header_grid` beroperasi secara simetris dari `-22pt` hingga `+22pt`.
- **Penegakan Mutlak Kanan <= Kiri**: Baik pada `ukur_kolom` maupun `ukur_layout_ketat`, kolom kanan yang lebih panjang daripada kolom kiri dikenakan penalti pelanggaran fatal (termasuk pada halaman terakhir dan segmen pra-objek penuh).
- **Auto-Fix Kolom Berbasis Grid 11 pt**: `aksi_auto_fix` pada editor blok secara ketat beroperasi pada grid 11 pt melalui pengangkatan footer simetris (`#kaki-naik` hingga maks 22 pt) dan penyetelan skala gambar bertahap, tanpa pernah mengubah properti tipografi (tracking, leading, spacing).
- **Layout Map v2**: Pemetaan koordinat interaktif blok dokumen halaman 1 kini dipetakan tepat di bawah area abstrak (`y >= 580 pt`), menjamin klik overlay visual tidak tumpang tindih.
