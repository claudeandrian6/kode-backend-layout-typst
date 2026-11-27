// =====================================================================
//  template_jurnal.typ — replika layout Scribus (BSSCD / PEDREV / RIETM)
//  Semua ukuran diambil dari PDF hasil Scribus (koordinat pt, A4).
//  Satu template untuk ketiga jurnal; beda jurnal = beda config.json.
// =====================================================================

// ---------- Warna (diukur dari PDF hasil Scribus) ----------
#let warna-teks   = rgb("#181715")   // hitam CMYK K100
#let warna-bagian = rgb("#a62686")   // "Ungu_Jurnal" — judul bagian ■
#let warna-ungu   = rgb("#662785")   // "Royal Purple" — header_info, abstrak
#let warna-garis  = rgb("#9d9c9c")   // garis header/footer (shade 50%)
#let warna-kotak  = rgb("#f9f6f7")   // isi kotak header halaman 1
#let warna-link   = rgb("#0000ff")

// Urutan font: yang pertama tersedia dipakai.
#let FONT = ("Times New Roman", "TeX Gyre Termes", "Liberation Serif")
#let FONT-MATH = ("Cambria Math", "STIX Two Math", "STIX Math", "New Computer Modern Math")

// ---------- Geometri halaman (pt) ----------
#let KIRI = 76.535pt
#let LEBAR-TEKS = 442.2pt
#let LEBAR-KOLOM = 213.6pt
#let JARAK-KOLOM = 15pt
// Baseline baris pertama isi = 111.2pt dari atas; 60 baris × 11pt per kolom.
#let MARGIN-ATAS = 102.4pt
// FIX-GRID60: 79.5pt membuat bawah area (762.39) 0.01pt di atas bawah kotak baris ke-60
// (762.40) sehingga hanya 59 baris muat. 79pt -> 60 baris seperti Scribus.
#let MARGIN-BAWAH = 79pt
#let TINGGI-AREA = 660pt

// Kotak baris = tepat 1em (0.8em di atas baseline, 0.2em di bawah),
// sehingga "leading" = jarak baseline − ukuran font, persis seperti
// "fixed line spacing" di Scribus.
#let tepi-atas = 0.8em
#let tepi-bawah = -0.2em

// Jarak vertikal agar baseline baris berikut berjarak `d` dari baseline
// baris sebelumnya (ukuran font a → b).
#let jarak(d, a, b) = v(d - 0.2 * a - 0.8 * b, weak: false)

#let sup-afiliasi(x) = box(text(size: 6pt, weight: "regular", baseline: -3.6pt, x))

// =====================================================================
//  Bagian-bagian halaman 1
// =====================================================================
#let kepala-hal-1(cfg, info) = {
  // header_info: "Bull. Soc. Stud. Commun. Dev., 5(3), 253-267, September, 2026"
  place(top + left, dx: KIRI, dy: 16.2pt, box(width: LEBAR-TEKS,
    align(center, text(9pt, weight: "bold", style: "italic", fill: warna-ungu, info))))
  // Kotak header + 2 garis
  place(top + left, dx: KIRI, dy: 30pt, rect(width: LEBAR-TEKS, height: 56.6pt,
    fill: warna-kotak, stroke: none))
  place(top + left, dx: KIRI, dy: 29.3pt, line(length: LEBAR-TEKS, stroke: 1pt + warna-teks))
  place(top + left, dx: KIRI, dy: 85.5pt, line(length: LEBAR-TEKS, stroke: 1pt + warna-teks))
  // Logo
  // FIX-LOGO-MUAT: logo jurnal dimuatkan utuh ke kotak 64x64pt (rasio dijaga, di tengah kotak),
  // seperti bingkai gambar Scribus; logo persegi (BSSCD) tampil identik dengan sebelumnya.
  // FIX-LOGO-SLA: geometri logo dari template Scribus jurnal (bingkai potong + posisi & lebar gambar, pt)
  let sla = cfg.at("logo_jurnal_sla", default: none)
  if sla == none {
    place(top + left, dx: 79.5pt, dy: 28.1pt, box(width: 64pt, height: 64pt,
      image(cfg.logo_jurnal, width: 64pt, height: 64pt, fit: "contain")))
  } else {
    let (bx, by, bw, bh) = sla.bingkai.map(v => v * 1pt)
    let (gx, gy, gw) = sla.gambar.map(v => v * 1pt)
    place(top + left, dx: bx, dy: by, box(width: bw, height: bh, clip: true,
      place(top + left, dx: gx - bx, dy: gy - by, image(cfg.logo_jurnal, width: gw))))
  }
  place(top + left, dx: 444.6pt, dy: 44.85pt, image(cfg.logo_penerbit, width: 66pt))
  // Teks tengah kotak
  let tengah(dy, isi) = place(top + left, dx: KIRI, dy: dy, box(width: LEBAR-TEKS, align(center, isi)))
  tengah(37.4pt, text(12pt, weight: "bold", cfg.nama_lengkap))
  // FIX-DOI-JURNAL: DOI jurnal di header (config "doi_jurnal"; default = doi_prefix artikel)
  let doi-jurnal = cfg.at("doi_jurnal", default: cfg.doi_prefix)
  tengah(56.1pt, text(8pt)[ISSN: #link("https://portal.issn.org/resource/ISSN/" + cfg.issn,
    text(fill: warna-link, cfg.issn)) | Journal's DOI: #link("https://doi.org/" + doi-jurnal,
    text(fill: warna-link, doi-jurnal))])
  tengah(71.6pt, text(8pt)[Homepage: #link(cfg.homepage, text(fill: warna-link, cfg.homepage))])
}

// FIX-JUDUL-SEGITIGA: judul artikel berbentuk segitiga terbalik yang rapi -- tiap baris tidak lebih
// panjang dari baris di atasnya. Dicari pemenggalan dengan baris pertama sepanjang mungkin, lalu baris
// kedua, dst. (urutan leksikografis), dengan jumlah baris alami; bila tidak ada, ditambah 1 baris.
#let _cari-segitiga(lebar, n, i, sisa, batas, W) = {
  if sisa == 1 {
    let w = lebar(i, n)
    return if w <= W and w <= batas { (n,) } else { none }
  }
  // sisakan minimal 1 kata untuk tiap baris berikutnya; baris terpanjang dicoba dulu
  for j in range(n - sisa + 1, i, step: -1) {
    let w = lebar(i, j)
    if w <= W and w <= batas {
      let r = _cari-segitiga(lebar, n, j, sisa - 1, w, W)
      if r != none { return (j,) + r }
    }
  }
  none
}

// unit-segitiga: potongan yang tidak boleh dipenggal (kata judul, atau satu nama penulis lengkap
// dengan nomor afiliasi & pemisahnya), disusun menjadi segitiga terbalik.
#let unit-segitiga(unit, gaya) = layout(ukuran => {
  let W = ukuran.width
  let n = unit.len()
  let lebar(i, j) = measure(gaya(unit.slice(i, j).join())).width
  if n < 2 or lebar(0, n) <= W { return align(center, gaya(unit.join())) }
  let k = 1
  let a = 0
  for j in range(1, n + 1) {
    if lebar(a, j) > W and j - 1 > a { k += 1; a = j - 1 }
  }
  for kk in range(k, k + 2) {
    let r = _cari-segitiga(lebar, n, 0, kk, W, W)
    if r != none {
      let baris = ()
      let awal = 0
      for b in r { baris.push(unit.slice(awal, b).join()); awal = b }
      return align(center, gaya(baris.join(linebreak())))
    }
  }
  align(center, gaya(unit.join()))
})

#let judul-segitiga(teks, gaya) = layout(ukuran => {
  let W = ukuran.width
  let kata = teks.split(" ").filter(k => k != "")
  let n = kata.len()
  let lebar(i, j) = measure(gaya(kata.slice(i, j).join(" "))).width
  if n < 2 or lebar(0, n) <= W { return align(center, gaya(teks)) }
  // jumlah baris alami (diisi penuh)
  let k = 1
  let a = 0
  for j in range(1, n + 1) {
    if lebar(a, j) > W and j - 1 > a { k += 1; a = j - 1 }
  }
  for kk in range(k, k + 2) {
    let r = _cari-segitiga(lebar, n, 0, kk, W, W)
    if r != none {
      let baris = ()
      let awal = 0
      for b in r { baris.push(kata.slice(awal, b).join(" ")); awal = b }
      return align(center, gaya(baris.map(x => [#x]).join(linebreak())))
    }
  }
  align(center, gaya(teks))
})

#let _tanda-meta(lbl, nilai: "awal") = block(height: 0pt, above: 0pt, below: 0pt, breakable: false,
  [#metadata(nilai)#label(lbl)])

#let blok-judul(meta, header-grid: 0pt) = {
  set par(justify: false, leading: 1pt, first-line-indent: 0pt)
  let d-judul = if header-grid == -22pt { 16pt } else if header-grid == -11pt { 20pt } else if header-grid == 11pt { 29pt } else if header-grid == 22pt { 33pt } else { 25pt }
  let d-bawah = if header-grid == -22pt { 5.4pt } else if header-grid == -11pt { 8.4pt } else if header-grid == 11pt { 12.4pt } else if header-grid == 22pt { 14.4pt } else { 10.4pt }
  _tanda-meta("meta-judul")
  judul-segitiga(meta.judul, it => text(13pt, weight: "bold", it))
  _tanda-meta("blk-akhir-m-judul", nilai: "akhir")
  jarak(d-judul, 13pt, 11pt)
  // FIX-PENULIS-SEGITIGA: baris penulis juga segitiga terbalik; satu nama penulis tidak pernah dipenggal,
  // tanda "&" tetap di tempatnya (ujung baris sebelumnya)
  let n = meta.penulis.len()
  let unit = meta.penulis.enumerate().map(((i, p)) => {
    let tanda = p.at("afiliasi", default: "") + if p.at("korespondensi", default: false) {"*"} else {""}
    let isi = [#p.nama#if tanda != "" { sup-afiliasi(tanda) }]
    // pemisah (koma dan "&") ikut nama SEBELUMNYA; yang pindah baris hanya nama + nomornya
    let belakang = if i < n - 2 { ", " } else if i == n - 2 { if n > 2 { ", & " } else { " & " } } else { "" }
    [#isi#belakang]
  })
  _tanda-meta("meta-penulis")
  unit-segitiga(unit, it => text(11pt, weight: "bold", it))
  _tanda-meta("blk-akhir-m-penulis", nilai: "akhir")
  // FIX-NOMOR-AFILIASI: nomor afiliasi (superskrip) sesuai nomor di naskah; bila naskah tidak
  // menulisnya tetapi penulis bertanda nomor dan afiliasi > 1 -> 1, 2, 3 ...
  let nomor-af = meta.at("afiliasi_nomor", default: ())
  let ada-tanda = meta.penulis.any(p => p.at("afiliasi", default: "") != "")
  _tanda-meta("meta-afiliasi")
  for (i, a) in meta.afiliasi.enumerate() {
    jarak(14pt, if i == 0 {11pt} else {10pt}, 10pt)
    let no = if i < nomor-af.len() and nomor-af.at(i) != "" { nomor-af.at(i) }
      else if ada-tanda and meta.afiliasi.len() > 1 { str(i + 1) } else { "" }
    align(center, text(10pt, { if no != "" { sup-afiliasi(no) }; a }))
  }
  _tanda-meta("blk-akhir-m-afiliasi", nilai: "akhir")
  jarak(d-bawah, 10pt, 0pt)
  block(width: 100%, height: 1pt, fill: warna-teks)
}

#let kolom-info(meta) = {
  set par(justify: false, leading: 3pt, first-line-indent: 0pt)
  let baris(isi, ukuran: 9pt, gaya: "normal", tebal: "regular", warna: warna-teks) = block(
    text(ukuran, style: gaya, weight: tebal, fill: warna, isi))
  baris(ukuran: 10pt, tebal: "bold")[ARTICLE INFO]
  jarak(12pt, 10pt, 9pt)
  baris(gaya: "italic", tebal: "bold")[Article History:]
  for (lbl, k) in (("Received", "diterima"), ("Accepted", "disetujui"), ("Published", "terbit")) {
    jarak(12pt, 9pt, 9pt)
    _tanda-meta("meta-riwayat-" + k)
    baris(lbl + ": " + meta.riwayat.at(k, default: ""))
    _tanda-meta("blk-akhir-m-riwayat-" + k, nilai: "akhir")
  }
  jarak(24pt, 9pt, 9pt)
  _tanda-meta("meta-kata-kunci")
  baris(gaya: "italic", tebal: "bold")[Keywords:]
  let kw = meta.kata_kunci
  for (i, k) in kw.enumerate() {
    jarak(12pt, 9pt, 9pt)
    baris(k + if i < kw.len() - 1 {";"} else {""})
  }
  _tanda-meta("blk-akhir-m-kata-kunci", nilai: "akhir")
  if meta.at("email", default: "") != "" {
    jarak(22pt, 9pt, 8pt)
    _tanda-meta("meta-email")
    baris(ukuran: 8pt, gaya: "italic", tebal: "bold")[\*Corresponding Author:]
    jarak(10pt, 8pt, 8pt)
    baris(ukuran: 8pt, warna: warna-link, link("mailto:" + meta.email, meta.email))
    _tanda-meta("blk-akhir-m-email", nilai: "akhir")
  }
}

#let kolom-abstrak(meta) = {
  set par(justify: true, leading: 0pt, first-line-indent: 0pt, spacing: 0pt)
  _tanda-meta("meta-abstrak")
  block(text(10pt, weight: "bold")[ABSTRACT])
  jarak(10pt, 10pt, 10pt)
  // Sub-judul abstrak terstruktur (sama dengan highlight_abstract_headers)
  show regex("(OBJECTIVES|METHODS|FINDINGS|CONCLUSION|TUJUAN|METODE|HASIL|KESIMPULAN):"): it => text(weight: "bold", fill: warna-ungu, it)
  // FIX-ABSTRAK-TERSTRUKTUR: sub-judul gaya kalimat di awal paragraf ("Purpose:", "Key Findings:")
  show regex("(?m)^(Purpose|Objectives?|Aims?|Background|Introduction|Context|Design|Methods?|Methodology|Approach|Key Findings|Findings|Results|Conclusions?|Implications|Originality|Novelty|Tujuan|Latar belakang|Metode|Hasil|Temuan|Kesimpulan|Implikasi|Kebaruan):"): it => text(weight: "bold", fill: warna-ungu, it)
  text(10pt, meta.abstrak)
  _tanda-meta("blk-akhir-m-abstrak", nilai: "akhir")
}

#let blok-sitasi(meta, sitasi, doi-url, lisensi) = {
  set par(justify: true, leading: 3pt, first-line-indent: 0pt, spacing: 0pt)
  set text(7pt)
  _tanda-meta("meta-sitasi")
  block(width: 100%, height: 1pt, fill: warna-teks)
  jarak(10.2pt, 0pt, 7pt)
  block(text(weight: "bold", style: "italic")[To cite this article:])
  jarak(10pt, 7pt, 7pt)
  block[#sitasi doi: #link(doi-url, text(fill: warna-link, doi-url))]
  jarak(13pt, 7pt, 7pt)
  block[This is an open access article under the CC BY SA licence (#link(lisensi, text(fill: warna-link, lisensi))).]
  jarak(5.8pt, 7pt, 0pt)
  block(width: 100%, height: 1pt, fill: warna-teks)
  _tanda-meta("blk-akhir-m-sitasi", nilai: "akhir")
}

// =====================================================================
//  Elemen isi artikel (dipanggil dari body.typ hasil build.py)
// =====================================================================

#let DI-TABEL = state("di-tabel", false)   // FIX-RUMUS-TABEL: rumus dalam tabel tidak di-grid
#let _INJEKSI-KUNCI = state("hal-injeksi-kunci", 0)
#let _tanpa-injeksi(isi) = {
  _INJEKSI-KUNCI.update(c => c + 1)
  isi
  _INJEKSI-KUNCI.update(c => calc.max(0, c - 1))
}

// Pergeseran footer: umum 11 pt untuk halaman non-akhir; khusus artikel 549 hingga 22 pt.
#let KAKI-NAIK = state("kaki-naik-target", (:))
#let kaki-naik(h, d) = KAKI-NAIK.update(x => { x.insert(str(h), d); x })
#let KEPALA-TURUN = state("kepala-turun-target", (:))
#let kepala-turun(h, d) = KEPALA-TURUN.update(x => { x.insert(str(h), d); x })
#let _HAL-INJEKSI-AKTIF = state("hal-injeksi-aktif", false)
#let _injeksi-boleh() = _HAL-INJEKSI-AKTIF.get() and _INJEKSI-KUNCI.get() == 0 and not DI-TABEL.get()
#let _titik-injeksi() = {
  [#metadata(1)#label("titik-inj")]
  context {
    let p = here().page()
    if p != none and p >= 2 {
      let base-t = if KEPALA-TURUN.final().values().any(v => v < 0pt) { 22pt } else { 0pt }
      let base-b = if KAKI-NAIK.final().values().any(v => v < 0pt) { 22pt } else { 0pt }
      let kt = calc.max(-22pt, calc.min(22pt, KEPALA-TURUN.final().at(str(p), default: 0pt)))
      let kn = calc.max(-22pt, calc.min(22pt, KAKI-NAIK.final().at(str(p), default: 0pt)))
      let ht = base-t + kt
      let hb = base-b + kn
      if ht > 0pt or hb > 0pt {
        let di-hal = query(<titik-inj>).filter(m => m.location().page() == p)
        if di-hal.len() > 0 and di-hal.first().location().position() == here().position() {
          if ht > 0pt {
            place(top, float: true, scope: "parent", clearance: 0pt, block(width: 100%, height: ht))
          }
          if hb > 0pt {
            place(bottom, float: true, scope: "parent", clearance: 0pt, block(width: 100%, height: hb))
          }
        }
      }
    }
  }
}

// FIX-PENANDA: penanda posisi nol-lebar di awal paragraf/judul (dibaca build.py)
#let pf(n, bagian) = [#metadata((n, bagian))#label("frag-awal-" + str(n) + "-" + str(bagian))]
#let pe(n, bagian: 0) = box(width: 0pt, height: 0pt, baseline: 0pt, [#metadata((n, bagian))#label("frag-akhir-" + str(n) + "-" + str(bagian))])
#let pm(n) = [#metadata(n)#label("pm-" + str(n))#pf(n, 0)]

#let _CTX-FUNC = ([#{context []}]).func()
#let _STYLED-FUNC = ([#set text(10pt); a]).func()
#let _SEQ-FUNC = ([a] + [b]).func()
#let _META-FUNC = metadata
#let _V-FUNC = v
#let _SPACE-FUNC = ([ ] + [a]).children.at(0).func()
#let _BLOK-FUNCS = (
  par, heading, block, pad, align, place, grid, table, stack, columns,
  layout, colbreak, pagebreak, list, enum, terms, figure, rect, square,
  circle, ellipse, line, polygon, v, math.equation,
)

#let _punya-elemen-blok(el) = {
  if el == none { return false }
  let f = el.func()
  if _BLOK-FUNCS.contains(f) { return true }
  if f == _SEQ-FUNC and el.has("children") {
    return el.children.any(c => _punya-elemen-blok(c))
  }
  if f == _STYLED-FUNC and el.has("child") {
    return _punya-elemen-blok(el.child)
  }
  false
}

#let _ratakan-seq(el) = {
  if el == none { return () }
  if el.func() == _SEQ-FUNC and el.has("children") {
    let out = ()
    for c in el.children {
      out += _ratakan-seq(c)
    }
    return out
  }
  return (el,)
}

// Ekstrak weak v(...) di tepi luar (mis. #wawancara, #rumus-teks) agar tetap berlaku di alur luar.
#let _ekstrak-v-tepi(el) = {
  let items = _ratakan-seq(el)
  if items.len() == 1 and items.at(0).func() == _STYLED-FUNC and items.at(0).has("child") {
    let sub = _ekstrak-v-tepi(items.at(0).child)
    return (pre: sub.pre, post: sub.post, inti: el)
  }
  let pre = ()
  let post = ()
  let i = 0
  while i < items.len() {
    let c = items.at(i)
    if c == none { i += 1; continue }
    let f = c.func()
    if f == _SPACE-FUNC or f == _META-FUNC or f == _CTX-FUNC {
      i += 1
    } else if f == _V-FUNC and c.at("weak", default: false) {
      pre.push(c)
      items.remove(i)
    } else {
      break
    }
  }
  let j = items.len() - 1
  while j >= 0 {
    let c = items.at(j)
    if c == none { j -= 1; continue }
    let f = c.func()
    if f == _SPACE-FUNC or f == _META-FUNC or f == _CTX-FUNC {
      j -= 1
    } else if f == _V-FUNC and c.at("weak", default: false) {
      post.insert(0, c)
      items.remove(j)
      j -= 1
    } else {
      break
    }
  }
  let inti = if pre.len() > 0 or post.len() > 0 {
    if items.len() > 0 { items.join() } else { none }
  } else {
    el
  }
  return (pre: pre, post: post, inti: inti)
}

#let beku(dx, dy, isi, boleh-pecah: false, tanpa-inden: false,
  freeze-w: auto, slot-h: auto, freeze-leading: auto, freeze-tracking: auto) = {
  let konten = if tanpa-inden {
    _tanpa-injeksi({
      set par(first-line-indent: 0pt)
      isi
    })
  } else {
    isi
  }
  if dx == 0pt and dy == 0pt and freeze-w == auto and slot-h == auto and freeze-leading == auto and freeze-tracking == auto {
    konten
  } else {
    context { if _injeksi-boleh() { _titik-injeksi() } }
    let ekstrak = _ekstrak-v-tepi(isi)
    let blok-mode = _punya-elemen-blok(isi)
    let konten-inti = if tanpa-inden {
      _tanpa-injeksi({
        set par(first-line-indent: 0pt)
        ekstrak.inti
      })
    } else if not blok-mode {
      // Pertahankan first-line-indent paragraf meski dibungkus block(...)
      [#h(0pt)#parbreak()#ekstrak.inti]
    } else {
      ekstrak.inti
    }
    let body-beku = layout(uk => context {
      let w-box = if freeze-w != auto { freeze-w } else { uk.width }
      let inner = _tanpa-injeksi(konten-inti)
      let inner = if freeze-tracking != auto { { set text(tracking: freeze-tracking); inner } } else { inner }
      let inner = if freeze-leading != auto { { set par(leading: freeze-leading); inner } } else { inner }
      let kontainer = block(width: w-box, above: 0pt, below: 0pt, inner)
      let sz = measure(kontainer)
      let h-slot = if slot-h != auto { slot-h } else { sz.height }
      block(width: 100%, height: h-slot, above: 0pt, below: 0pt, breakable: false,
        place(top + left, dx: dx, dy: dy, kontainer))
    })
    for pv in ekstrak.pre { pv }
    body-beku
    for pv in ekstrak.post { pv }
  }
}

#let beku-kol(dx, dy, tanpa-inden: false, ..bagian) = {
  for (i, item) in bagian.pos().enumerate() {
    if i > 0 { colbreak(weak: true) }
    beku(dx, dy, item, tanpa-inden: if i == 0 { tanpa-inden } else { true })
  }
}

// Ruang sebelum judul bagian/sub-bagian; otomatis hilang di puncak kolom.
#let celah() = v(11pt, weak: true)

// Keterangan Figure/Table: label tebal, rata kiri-kanan, baris terakhir di tengah.
#let keterangan(label, isi) = layout(size => _tanpa-injeksi({
  // FIX-KET-REGULER-ISI: isi keterangan selalu reguler/tegak (hanya label yang tebal),
  // meskipun penulis memiringkan seluruh caption di Word.
  let body = text(10pt)[#text(weight: "bold", label) #text(weight: "regular", style: "normal", isi)]
  let p = par(justify: true, leading: 1pt, first-line-indent: 0pt, body)
  // Ukur lebar teks jika ditulis dalam satu baris (tanpa justify)
  let m = measure(par(justify: false, leading: 1pt, first-line-indent: 0pt, body))
  if m.width <= size.width {
    align(center, p)
  } else {
    align(left, p)
  }
}))

// FIX-CATATAN-OBJEK: "Note." / "Source:" milik gambar/tabel ditaruh di bawah objeknya (9 pt, rata
// kiri-kanan, tanpa indent) agar ikut berpindah bersama objek.
#let catatan-objek(isi) = block(width: 100%, above: 4pt, below: 0pt, _tanpa-injeksi({
  set par(justify: true, leading: 1.5pt, first-line-indent: 0pt, spacing: 2pt)
  set text(9pt)
  set align(left)
  isi
}))

// FIX-KET-STICKY: keterangan tabel SELALU sticky dan menempel pada badan tabelnya, termasuk
// pada tabel yang boleh terpotong lintas kolom/halaman. Jarak 5 pt tetap dipertahankan.
#let ket-tabel(label, isi) = block(width: 100%, sticky: true, below: 5pt,
  keterangan(label, isi))

// FIX-GARIS-TABEL: tabel yang di Word tanpa garis antarbaris -> hanya garis atas, bawah kepala
// (table.hline dari pandoc) dan bawah tabel
// FIX-KEPALA-TEBAL: baris kepala tabel tebal; FIX-TABEL-ISI: tabel selebar isinya, di tengah
#let badan-tabel(badan, garis-baris, kepala: 1) = {
  set par(justify: false, leading: 0pt, spacing: 0pt, first-line-indent: 0pt, hanging-indent: 0pt)
  set table(align: left + horizon, inset: (x: 5pt, y: 4pt))
  set table(stroke: (x, y) => (top: if y == 0 or y == kepala { 0.8pt } else { none })) if not garis-baris
  show table.cell: it => {
    set align(left)
    set par(justify: false, leading: 0pt, spacing: 0pt, first-line-indent: 0pt, hanging-indent: 0pt)
    if it.y < kepala { set text(weight: "bold") }
    it
  }
  align(center, badan)
}

// FIX-TABEL-10: standar ukuran teks isian tabel = 10 pt (seragam dengan caption tabel & gambar)
#let UKURAN-TABEL = 10pt
#let BATAS-TINGGI = 450pt   // sisakan ruang untuk keterangan
// FIX-TABEL-KECIL-UTUH: tabel pendek (<= separuh area teks 660 pt) tidak boleh dipotong;
// seluruh blok (keterangan + tabel) dipindah utuh ke kolom/halaman berikutnya.
#let BATAS-TABEL-UTUH = 330pt
// FIX-TINGGI-LENTUR: BATAS-TINGGI = tinggi AWAL gambar yang tinggi. Looping ukuran gambar (build.py)
// boleh memperkecil gambar selebar halaman sampai 70% atau memperbesarnya sampai BATAS-TINGGI-MAKS
// (lebar tetap <= lebar teks) agar kolom rata dan tanpa baris tunggal.
#let BATAS-TINGGI-MAKS = 540pt

// Gambar: lebar ≤ 1,25× kolom di Word → 1 kolom; selebihnya → selebar halaman.
// Tinggi berlebih diperkecil proporsional (sama dengan aturan Scribus).
// FIX-DI-TEMPAT: objek selebar halaman diletakkan di posisi naskah. Teks sebelum
// dan sesudahnya berada di #kolom[...] terpisah (kolom seimbang), sehingga urutan baca
// kiri-atas -> kanan-atas -> objek -> kiri-bawah -> kanan-bawah. Bila objek tidak muat
// di sisa halaman, objek naik ke atas halaman berikut dan teks berikutnya mengisi sisa halaman.
// FIX-PENANDA-SEGMEN: blok nol-tinggi di awal & akhir tiap segmen dua kolom (dibaca build.py)
#let tanda-seg(jenis) = { counter("segmen").step(); context block(height: 0pt, above: 0pt, below: 0pt,
  [#metadata(jenis)#label("seg-" + jenis + "-" + str(counter("segmen").get().first()))]) }
#let tanda-seg-akhir(jenis) = context block(height: 0pt, above: 0pt, below: 0pt,
  [#metadata(jenis)#label("seg-akhir-" + str(counter("segmen").get().first()))])
// FIX-SEIMBANG-GENAP: penyeimbang Typst mengisi kolom kiri selama tingginya <= target (= total/2),
// sehingga bila jumlah baris halaman akhir genap kiri kelebihan 1 baris (kanan pendek 2 baris).
// Spasi -0,5 baris di akhir segmen menurunkan target 2,75pt: hasil kiri = ceil(N/2), kanan = floor(N/2).
#let KOREKSI-SEIMBANG = -5.5pt
// FIX-KOREKSI-SEGMEN: koreksi dapat diubah per segmen oleh build.py (ratakan_kolom)
#let kolom(isi, koreksi: KOREKSI-SEIMBANG) = columns(2, gutter: JARAK-KOLOM, tanda-seg("awal")
  + isi + tanda-seg-akhir("akhir") + v(koreksi))
// FIX-ATURAN-OBJEK: segmen sebelum objek yang tidak muat: kiri penuh, kanan boleh menggantung
#let kolom-lanjut(isi) = columns(2, gutter: JARAK-KOLOM, balanced: false,
  tanda-seg("lanjut") + isi + tanda-seg-akhir("akhir"))
// FIX-KOLOM-DI-TEMPAT: lingkup "column" untuk objek satu kolom (aturan sama, dalam kolom)
// FIX-KEPUTUSAN-LUAR: keputusan "tempat"/"apung" dari build.py (iterasi di luar Typst);
// auto = keputusan lama di dalam Typst.
// FIX-NOMOR-OBJEK: nomor objek dari build.py (tetap benar walau objek ditunda/dipindah)
#let lebar-di-tempat(isi-asli, lingkup: "parent", keputusan: auto, nomor: auto, frozen: false, freeze-x: 0pt, freeze-y: 0pt, slot-h: auto) = {
  context { if _injeksi-boleh() { _titik-injeksi() } }
  let isi-asli = _tanpa-injeksi(isi-asli)
  if nomor == auto { counter("objek-lebar").step() } else { counter("objek-lebar").update(nomor) }
  // FIX-POSISI-PENANDA: penanda nol-tinggi SEBELUM objek. here() di dalam context ikut
  // berpindah bila bloknya terdorong ke halaman berikut, sedangkan penanda ini tetap di
  // ujung teks sebelumnya, sehingga sisa ruang diukur dari tempat objek seharusnya mulai.
  // FIX-POSISI-BLOK: metadata lepas ikut menempel ke elemen berikutnya; dibungkus blok 0pt
  context block(height: 0pt, above: 0pt, below: 0pt, breakable: false,
    [#metadata("posisi")#label("objek-posisi-" + str(counter("objek-lebar").get().first()))])
  context {
  // FIX-FLOAT-TITIK: penanda per objek; bila iterasi sebelumnya sudah mengapungkannya
  // ke atas halaman ini, tingginya dikembalikan ke sisa ruang sebelum memutuskan.
  let nomor = counter("objek-lebar").get().first()
  let lbl = label("objek-lebar-" + str(nomor))
  let isi = [#metadata(nomor)#lbl] + isi-asli
  let tinggi_info = measure(block(width: if lingkup == "parent" { LEBAR-TEKS } else { LEBAR-KOLOM },
    isi-asli)).height.pt() - (if keputusan == "bawah" { 4.0 } else { 0.0 })
  let tinggi = measure(block(width: if lingkup == "parent" { LEBAR-TEKS } else { LEBAR-KOLOM },
    isi-asli)).height
  let h-jejak = if slot-h != auto { slot-h + (if keputusan == "bawah" { 4pt } else { 0pt }) } else { tinggi }
  let meta-info = if slot-h != auto or freeze-y != 0pt {
    (nomor, tinggi_info, (h-jejak - freeze-y).pt())
  } else {
    (nomor, tinggi_info)
  }
  let isi = [#metadata(meta-info)#label("objek-info-" + str(nomor))] + isi
  let jejak-apung = calc.ceil((h-jejak + 8pt) / 11pt - 0.001) * 11pt   // FIX-JARAK-FLOAT: min. 8 pt di bawah float
  let w-target = if lingkup == "parent" { LEBAR-TEKS } else { LEBAR-KOLOM }
  let isi-konten = if freeze-x != 0pt or freeze-y != 0pt or slot-h != auto {
    block(width: w-target, height: h-jejak, above: 0pt, below: 0pt, breakable: false,
      place(top + left, dx: freeze-x, dy: freeze-y, block(width: w-target, isi)))
  } else {
    isi
  }
  // FIX-APUNG-TANPA-KUERI: objek yang ditunda (apung) dipasang langsung sebagai float tanpa
  // membaca posisi penanda; kueri posisi di sini membuat tata letak tidak konvergen.
  if keputusan == "apung" {
    return place(top, float: true, scope: lingkup, clearance: jejak-apung - h-jejak, isi-konten)
  }
  // FIX-PITA-APUNG: pita teks < 5 baris di atas/bawah objek -> objek ke atas/dasar halaman yang sama
  if keputusan == "atas" or keputusan == "bawah" {
    // FIX-OVERFLOW-BAWAH: place(bottom) mengikat dasar blok ke baseline.
    // Line-leading baris terakhir caption membuatnya melewati area teks 4pt.
    // Spacer ini menarik isi float 4pt ke atas, memperbaiki bounding box tanpa merusak grid.
    let isi-float = if keputusan == "bawah" { isi-konten + v(4pt) } else { isi-konten }
    return place(if keputusan == "atas" { top } else { bottom }, float: true, scope: lingkup,
      clearance: jejak-apung - h-jejak, isi-float)
  }
  // FIX-GRID-OBJEK: tinggi total objek (+ jarak atas/bawah) dibulatkan ke kelipatan 11pt
  // agar teks sesudahnya tetap di grid baris; sisa 1 baris per kolom dilarang.
  let jejak(min-celah) = calc.ceil((h-jejak + min-celah) / 11pt - 0.001) * 11pt
  let normal = jejak(22pt)
  // FIX-OBJEK-GRID-ATAS: jarak atas/bawah DI DALAM blok; spasi blok (above) hilang bila objek
  // terdorong ke puncak halaman sehingga teks sesudahnya keluar dari grid 11 pt.
  let tempatkan(total) = block(above: 0pt, below: 0pt, breakable: false, {
    v((total - h-jejak) / 2, weak: false)
    isi-konten
    v((total - h-jejak) / 2, weak: false)
  })
  if keputusan == "apung" {
    place(top, float: true, scope: lingkup, clearance: jejak-apung - h-jejak, isi-konten)
  } else {
    // FIX-TEMPAT-STABIL: objek \"tempat\" selalu in-flow tanpa query posisi/pagebreak.
    // Query + pagebreak di dalam container membuat Typst tidak konvergen dan tidak sah.
    tempatkan(normal)
  }
} }

#let _gambar-potong(path, potong, lebar: auto, tinggi: auto) = {
  if potong == none {
    if tinggi != auto { image(path, height: tinggi) } else { image(path, width: lebar) }
  } else {
    let (cl, ct, cr, cb) = potong
    let fw = calc.max(0.05, 1.0 - cl - cr)
    let fh = calc.max(0.05, 1.0 - ct - cb)
    let dasar = measure(image(path, width: 100pt))
    let rasio-asli = dasar.height / 100pt
    let rasio-crop = (rasio-asli * fh) / fw
    let w-tampil = if tinggi != auto { tinggi / rasio-crop } else { lebar }
    let h-tampil = w-tampil * rasio-crop
    let w-penuh = w-tampil / fw
    let h-penuh = w-penuh * rasio-asli
    box(width: w-tampil, height: h-tampil, clip: true,
      place(top + left, dx: -cl * w-penuh, dy: -ct * h-penuh, image(path, width: w-penuh)))
  }
}

#let _tabel-potong(lebar, potong, isi) = {
  if potong == none {
    isi
  } else {
    let (cl, ct, cr, cb) = potong
    let fw = calc.max(0.05, 1.0 - cl - cr)
    let fh = calc.max(0.05, 1.0 - ct - cb)
    let penuh-blok = block(width: lebar, breakable: false, isi)
    let u = measure(penuh-blok)
    let w-tampil = u.width * fw
    let h-tampil = u.height * fh
    align(center, box(width: w-tampil, height: h-tampil, clip: true,
      place(top + left, dx: -cl * u.width, dy: -ct * u.height, penuh-blok)))
  }
}

#let gambar(path, lebar-word: 0pt, label: [], isi: [], penuh: auto, di-tempat: false,
  lebar-tampil: auto, keputusan: auto, skala: 1.0, nomor: auto, catatan: none, potong: none,
  frozen: false, freeze-x: 0pt, freeze-y: 0pt, slot-h: auto) = {
  let penuh = if penuh == auto { lebar-word > LEBAR-KOLOM * 1.25 } else { penuh }
  let slot = if penuh { LEBAR-TEKS } else { LEBAR-KOLOM }
  // FIX-UKURAN-CHART: chart ditampilkan pada lebar yang membuat font-nya = font narasi
  let lebar-img = calc.min(slot, (if lebar-tampil == auto { slot } else { calc.min(lebar-tampil, slot) }) * skala)
  let isi-gambar = block(width: slot, {
      context {
        let g-uji = _gambar-potong(path, potong, lebar: lebar-img)
        let ukuran = measure(g-uji)
        if ukuran.height > BATAS-TINGGI * skala {   // FIX-SKALA-BATAS
          align(center, _gambar-potong(path, potong, tinggi: BATAS-TINGGI * skala))
        } else {
          align(center, g-uji)
        }
      }
      // FIX-GAMBAR-TANPA-KET: gambar tanpa keterangan tidak diberi baris keterangan kosong
      if label != [] or isi != [] {
        v(6pt)
        keterangan(label, isi)
      }
      if catatan != none { catatan-objek(catatan) }
    })
  if di-tempat { return lebar-di-tempat(isi-gambar, lingkup: if penuh { "parent" } else { "column" },
    keputusan: keputusan, nomor: nomor, frozen: frozen, freeze-x: freeze-x, freeze-y: freeze-y, slot-h: slot-h) }
  let isi-fig = block(width: slot, {
      context {
        let g-uji = _gambar-potong(path, potong, lebar: slot)
        let ukuran = measure(g-uji)
        if ukuran.height > BATAS-TINGGI {
          align(center, _gambar-potong(path, potong, tinggi: BATAS-TINGGI))
        } else {
          align(center, g-uji)
        }
      }
      v(6pt)
      keterangan(label, isi)
    })
  let isi-final = if freeze-x != 0pt or freeze-y != 0pt or slot-h != auto {
    context {
      let sz = measure(isi-fig)
      let h-slot = if slot-h != auto { slot-h } else { sz.height }
      block(width: 100%, height: h-slot, above: 0pt, below: 0pt, breakable: false,
        place(top + left, dx: freeze-x, dy: freeze-y, isi-fig))
    }
  } else {
    isi-fig
  }
  figure(
    placement: auto,
    scope: if penuh { "parent" } else { "column" },
    kind: image, supplement: none, numbering: none, gap: 0pt,
    _tanpa-injeksi(isi-final),
  )
}

// FIX-TABEL-TERPOTONG: (mode di-tempat) tabel SELALU di posisi naskah, boleh terpotong
// lintas kolom/halaman dengan judul kolom diulang; keterangan menempel pada awal tabel.
// Sesudah tabel teks kembali ke grid 11pt, dan sisa 1 baris per kolom dilarang.
#let tabel-di-tempat(lbl-ket, isi-ket, badan, lingkup: "parent", nomor: auto, potong: none,
  frozen: false, freeze-x: 0pt, freeze-y: 0pt, slot-h: auto, boleh-pecah: false) = {
  if nomor == auto { counter("objek-lebar").step() } else { counter("objek-lebar").update(nomor) }
  context block(height: 0pt, above: 0pt, below: 0pt, breakable: false,
    [#metadata("posisi")#label("objek-posisi-" + str(counter("objek-lebar").get().first()))])
  // FIX-TABEL-STABIL: tabel tetap di aliran teks dan boleh terpotong. Jangan mengukur
  // caption/badan di dalam context query (measure -> layout -> query) karena Typst
  // tidak konvergen (counter(page)/here() menjadi none selama iterasi).
  context {
    let nomor = counter("objek-lebar").get().first()
    let lebar-t = if lingkup == "parent" { LEBAR-TEKS } else { LEBAR-KOLOM }
    // FIX-OBJEK-BERURUTAN: tepat di bawah objek sebelumnya (halaman sama, < 2,5 baris
    // di atas penanda) -> tanpa jarak atas tambahan.
    let atas = 11pt
    let q-ini = query(label("objek-posisi-" + str(nomor)))
    if q-ini.len() > 0 and nomor > 1 {
      let p-ini = q-ini.first().location()
      let q-akhir = query(label("objek-akhir-" + str(nomor - 1)))
      let q-info = query(label("objek-info-" + str(nomor - 1)))
      let bawah-lalu = if q-akhir.len() > 0 {
        (q-akhir.first().location().page(), q-akhir.first().location().position().y)
      } else if q-info.len() > 0 and q-info.first().value.at(1) > 0 {
        let slot-info = q-info.first().value
        let h-prev = if slot-info.len() > 2 { slot-info.at(2) } else { slot-info.at(1) }
        (q-info.first().location().page(), q-info.first().location().position().y
          + h-prev * 1pt)
      } else { none }
      if bawah-lalu != none and bawah-lalu.at(0) == p-ini.page() and p-ini.position().y - bawah-lalu.at(1) >= -1pt and p-ini.position().y - bawah-lalu.at(1) < 27.5pt {
        atas = 0pt
      }
    }
    let isi-tabel = if potong != none {
      let isi-penuh = {
        keterangan(lbl-ket, isi-ket)
        set text(UKURAN-TABEL)
        set par(justify: false, leading: 1pt, first-line-indent: 0pt)
        set align(left)
        set table.cell(breakable: true)
        badan
      }
      {
        block(width: 100%, height: 0pt, above: 0pt, below: 0pt, breakable: false,
          [#metadata((nomor, 0.0))#label("objek-info-" + str(nomor))])
        _tabel-potong(lebar-t, potong, isi-penuh)
      }
    } else {
      {
        // FIX-KET-TENGAH: blok keterangan selebar tabel agar caption di tengah tabel
        block(width: 100%, sticky: true, below: 5pt,
          [#metadata((nomor, 0.0))#label("objek-info-" + str(nomor))] + keterangan(lbl-ket, isi-ket))
        set text(UKURAN-TABEL)
        set par(justify: false, leading: 1pt, first-line-indent: 0pt)
        set align(left)
        set table.cell(breakable: true)   // FIX-BARIS-UTUH: baris tabel tidak dibelah
        badan
      }
    }
    if freeze-x != 0pt or freeze-y != 0pt or slot-h != auto {
      let kontainer = block(width: lebar-t, breakable: false, {
        isi-tabel
        block(height: 0pt, above: 0pt, below: 0pt, breakable: false,
          [#metadata("akhir-beku")#label("objek-akhir-beku-" + str(nomor))])
      })
      let sz = measure(kontainer)
      let h-slot = if slot-h != auto { slot-h } else { sz.height }
      block(width: lebar-t, height: h-slot, above: atas, below: 0pt, breakable: false,
        place(top + left, dx: freeze-x, dy: freeze-y, kontainer))
    } else {
      block(width: lebar-t, above: atas, below: 0pt, breakable: potong == none, isi-tabel)
    }
    block(height: 0pt, above: 0pt, below: 0pt, breakable: false, [
      #metadata((jenis: "objek-slot-akhir", nomor: nomor))
      #metadata("akhir")#label("objek-akhir-" + str(nomor))
    ])
  }
  context {
    let nomor = counter("objek-lebar").get().first()
    let q-akhir = query(label("objek-akhir-" + str(nomor)))
    if q-akhir.len() == 0 { return v(11pt, weak: true) }
    let akhir = q-akhir.first().location()
    let rel = akhir.position().y - MARGIN-ATAS
    let pad = calc.ceil(rel / 11pt - 0.001) * 11pt - rel
    v(pad + 11pt, weak: true)
  }
}

// Tabel: keterangan di atas. Kolom > 3 → selebar halaman.
#let tabel(jumlah-kolom: 0, label: [], isi: [], penuh: auto, di-tempat: false, keputusan: auto,
  nomor: auto, garis-baris: true, kepala: 1, potong: none, frozen: false, freeze-x: 0pt, freeze-y: 0pt, slot-h: auto, boleh-pecah: false, badan) = {
  let badan = badan-tabel(badan, garis-baris, kepala: kepala)
  let penuh = if penuh == auto { jumlah-kolom > 3 } else { penuh }
  if di-tempat and keputusan not in ("apung", "atas", "bawah") {   // FIX-PITA-APUNG
    return tabel-di-tempat(label, isi, badan, lingkup: if penuh { "parent" } else { "column" },
      nomor: nomor, potong: potong, frozen: frozen, freeze-x: freeze-x, freeze-y: freeze-y, slot-h: slot-h, boleh-pecah: boleh-pecah)
  }
  let w-tab = if penuh { LEBAR-TEKS } else { LEBAR-KOLOM }
  let isi-tab = {
    ket-tabel(label, isi)
    set text(UKURAN-TABEL)
    set par(justify: false, leading: 1pt, first-line-indent: 0pt)
    set align(left)
    badan
  }
  let wadah-tab = if potong != none {
    block(width: w-tab, breakable: false, context _tabel-potong(w-tab, potong, isi-tab))
  } else {
    block(width: w-tab, breakable: true, isi-tab)
  }
  if di-tempat {
    return lebar-di-tempat(wadah-tab, lingkup: if penuh { "parent" } else { "column" },
      keputusan: keputusan, nomor: nomor, frozen: frozen, freeze-x: freeze-x, freeze-y: freeze-y, slot-h: slot-h)
  }
  let isi-final = if freeze-x != 0pt or freeze-y != 0pt or slot-h != auto {
    context {
      let sz = measure(wadah-tab)
      let h-slot = if slot-h != auto { slot-h } else { sz.height }
      block(width: 100%, height: h-slot, above: 0pt, below: 0pt, breakable: false,
        place(top + left, dx: freeze-x, dy: freeze-y, wadah-tab))
    }
  } else {
    wadah-tab
  }
  figure(
    placement: auto,
    scope: if penuh { "parent" } else { "column" },
    kind: table, supplement: none, numbering: none, gap: 0pt,
    _tanpa-injeksi(isi-final),
  )
}

// Tabel sangat panjang: tidak mengambang, boleh terpotong lintas halaman
// pada halaman 1-kolom tersendiri.
#let tabel-panjang(label: [], isi: [], di-tempat: false, garis-baris: true, kepala: 1, potong: none,
  frozen: false, freeze-x: 0pt, freeze-y: 0pt, slot-h: auto, boleh-pecah: false, badan) = {
  let badan = badan-tabel(badan, garis-baris, kepala: kepala)
  let isi-tp = {
    ket-tabel(label, isi)
    set text(UKURAN-TABEL)
    set par(justify: false, leading: 1pt, first-line-indent: 0pt)
    if di-tempat { set align(left) }
    badan
  }
  if di-tempat {
    let wadah = if potong != none {
      context _tabel-potong(LEBAR-TEKS, potong, isi-tp)
    } else {
      isi-tp
    }
    if freeze-x != 0pt or freeze-y != 0pt or slot-h != auto {
      context {
        let sz = measure(block(width: LEBAR-TEKS, breakable: false, wadah))
        let h-slot = if slot-h != auto { slot-h } else { sz.height }
        block(width: LEBAR-TEKS, height: h-slot, above: 11pt, below: 11pt, breakable: false,
          place(top + left, dx: freeze-x, dy: freeze-y, block(width: LEBAR-TEKS, wadah)))
      }
    } else {
      if potong != none {
        block(above: 11pt, below: 11pt, breakable: false, wadah)
      } else {
        block(above: 11pt, below: 11pt, breakable: true, wadah)
      }
    }
  } else {
    let wadah = if potong != none {
      block(breakable: false, context _tabel-potong(LEBAR-TEKS, potong, isi-tp))
    } else {
      block(breakable: true, isi-tp)
    }
    page(columns: 1, margin: (left: KIRI, right: KIRI, top: MARGIN-ATAS, bottom: MARGIN-BAWAH),
      if freeze-x != 0pt or freeze-y != 0pt or slot-h != auto {
        context {
          let sz = measure(wadah)
          let h-slot = if slot-h != auto { slot-h } else { sz.height }
          block(width: 100%, height: h-slot, above: 0pt, below: 0pt, breakable: false,
            place(top + left, dx: freeze-x, dy: freeze-y, wadah))
        }
      } else {
        wadah
      }
    )
  }
}

// Paragraf dengan indent gantung (daftar, referensi)
// FIX-PAR: properti paragraf untuk konten kiriman harus lewat par() eksplisit
#let gantung(lebar, isi) = par(justify: true, hanging-indent: lebar, first-line-indent: 0pt, isi) // FIX-JUSTIFY
// FIX-LABEL-GANTUNG: daftar berlabel ("QA1: ...", "H1: ...") — label di margin, kata pertama pada
// tab stop 25pt, baris lanjutan menggantung 25pt (label lebih lebar: tab stop sesudah label + 3pt).
#let label-gantung(lbl, isi) = context {
  let w = calc.max(25pt, measure(lbl).width + 3pt)
  par(justify: true, hanging-indent: w, first-line-indent: 0pt)[#box(width: w, lbl)#isi]
}
// Referensi mengikuti aturan minimal dua baris pada setiap fragmen.
#let MODE-DI-TEMPAT = state("mode-di-tempat", false)
// FIX-SEJAJARKAN: lanjutan paragraf/entri yang dipotong build.py agar kolom kiri & kanan sejajar
// (baris terakhir kolom kanan dipindah ke halaman berikut). Lanjutan referensi menjorok 25pt
// seperti baris lanjutan entri; lanjutan narasi tanpa indent baris pertama.

// FIX-BUTIR: grid 15pt | isi — hanging-indent + box penanda membuat baris tidak penuh
// FIX-BUTIR-REKAT: rekat: true membuat butir menempel ke blok sesudahnya (paragraf isi),
// dipakai untuk daftar yang menjadi satu kesatuan dengan judul sub/subsub di atasnya.
#let butir(penanda, isi, rekat: false) = {
  context { if _injeksi-boleh() { _titik-injeksi() } }
  block(sticky: rekat, _tanpa-injeksi(
    grid(columns: (15pt, 1fr), penanda, par(justify: true, first-line-indent: 0pt, isi))))
}

#let lanjutan-ref(isi) = pad(left: 25pt, par(justify: true, first-line-indent: 0pt, hanging-indent: 0pt, isi))
#let lanjutan(isi) = par(justify: true, first-line-indent: 0pt, isi)

// Semua fragmen referensi wajib minimal dua baris.
#let referensi(isi) = {
  set text(costs: (widow: 100%, orphan: 100%))
  gantung(25pt, isi)
}
//semula
//#let wawancara(isi) = {
//  v(11pt, weak: true)
//  pad(left: 25pt, par(first-line-indent: 0pt, text(style: "italic", isi)))
//  v(11pt, weak: true)
//}

// Format wawancara: indent 15pt, jarak vertikal 11pt, semua teks miring seragam
#let wawancara(isi) = {
  context { if _injeksi-boleh() { _titik-injeksi() } }
  v(11pt, weak: true)
  pad(left: 15pt, _tanpa-injeksi({
    set text(style: "italic")
    show emph: it => it.body   // Netralkan pembalikan emph oleh Typst agar teks kutipan tetap miring
    set par(first-line-indent: 0pt, justify: true, leading: 0pt)
    isi
  }))
  v(11pt, weak: true)
}

// FIX-GRID-RUMUS-SEBARIS: rumus sebaris (inline) yang lebih tinggi dari kotak baris membuat
// baris itu lebih tinggi beberapa pt sehingga semua baris sesudahnya keluar dari grid 11 pt.
// Rumus diukur pada batas glyph sebenarnya: bila masih muat di ruang antarbaris (maks. 9 pt di
// atas & 3,5 pt di bawah garis dasar, kelebihan total <= 1,5 pt) tinggi baris tetap 11 pt
// (seperti fixed line spacing Scribus); bila tidak, baris diberi tepat 1 (atau lebih) baris
// tambahan agar grid tetap utuh. Hanya untuk teks 11 pt (bukan keterangan/abstrak).
#let DI-TABEL = state("di-tabel", false)   // FIX-RUMUS-TABEL: rumus dalam tabel tidak di-grid
#let RUMUS-PECAH = state("rumus-pecah", false)   // FIX-RUMUS-PECAH: rumus dipecah tidak dikotakkan
#let rumus-sebaris(it) = context {
  if text.size != 11pt or DI-TABEL.get() or RUMUS-PECAH.get() { return it }
  let ukur(x) = measure({ set text(top-edge: "bounds", bottom-edge: "bounds"); x })
  let tinggi = ukur(it)
  let turun = ukur([#box(height: 100pt)#it]).height - 100pt
  let naik = tinggi.height - turun
  let atas = calc.max(0pt, naik - 9pt)
  let bawah = calc.max(0pt, turun - 3.5pt)
  let isi = box(width: tinggi.width, height: 0pt, baseline: 0pt, place(top + left, dy: -naik,
    { set text(top-edge: "bounds", bottom-edge: "bounds"); it }))
  if atas + bawah <= 1.5pt { return isi }
  let tambah = calc.ceil((atas + bawah) / 11pt - 0.001) * 11pt
  let sisa = (tambah - atas - bawah) / 2
  // penyangga tak terlihat: naik 8,8 pt + atas + sisa, turun 2,2 pt + bawah + sisa
  box(width: 0pt, height: 11pt + tambah, baseline: 2.2pt + bawah + sisa) + isi
}

// FIX-RUMUS-BERNOMOR: rumus display di tengah kolom, nomor "(n)" rata kanan pada baris yang sama.
// Kolom kiri selebar nomor menjaga rumus tetap tepat di tengah.
// FIX-RUMUS-PECAH: rumus yang harus diperkecil di bawah 80% agar muat di kolom dipecah menjadi
// beberapa baris pada operator tingkat atas (=, +, -, ...) dengan ukuran penuh 11 pt (baris
// lanjutan menjorok), bila setiap potongannya muat; selain itu tetap diperkecil.
#let OPERATOR-PECAH = ("=", "+", "−", "-", "<", ">", "≤", "≥", "≈", "≠", "×", "·")
#let isi-rumus(isi) = if isi.func() == math.equation { isi } else if isi.has("children") {
  isi.children.find(c => c.func() == math.equation) } else { none }
// Kurung otomatis (lr) dilepas agar rumus boleh dipecah di operator di dalam kurung
#let anak-rata(x) = {
  let hasil = ()
  let daftar = if x.has("children") { x.children } else { (x,) }
  for c in daftar {
    if c.func() == math.lr { hasil += anak-rata(c.body) }
    else if c.func() == math.attach and c.base.has("children") and c.base.children.len() > 1 {
      // grup ber-subskrip {a + b}_(it): subskrip ditempel ke unsur terakhir grup
      let isi = anak-rata(c.base)
      let akhir = isi.pop()
      hasil += isi
      hasil.push(math.attach(akhir, t: c.at("t", default: none), b: c.at("b", default: none),
        tr: c.at("tr", default: none), br: c.at("br", default: none)))
    }
    else if c.has("children") and c.func() == [].func() { hasil += anak-rata(c) }
    else { hasil.push(c) }
  }
  hasil
}
#let potongan-terlebar(eq) = {
  let anak = anak-rata(eq.body)
  let seg = ()
  let cur = ()
  for c in anak {
    if c.has("text") and c.text in OPERATOR-PECAH and cur.len() > 0 { seg.push(cur); cur = (c,) }
    else { cur.push(c) }
  }
  seg.push(cur)
  if seg.len() < 2 { return none }
  calc.max(..seg.map(s => measure(math.equation(math.display(s.join()), block: false)).width.pt())) * 1pt
}
#let rumus-baris(eq, nomor) = context {
  let isi = block(above: 0pt, below: 0pt, inset: (left: 12.5pt), {
    RUMUS-PECAH.update(true)
    par(justify: false, first-line-indent: 0pt, hanging-indent: 25pt, leading: 5pt, {
      math.equation(math.display(anak-rata(eq.body).join()), block: false)
      if nomor != none { h(1fr); text(11pt, nomor) }
    })
    RUMUS-PECAH.update(false)
  })
  // tinggi total dibulatkan ke kelipatan 11 pt (+ jarak atas/bawah) agar grid baris tetap utuh
  layout(uk => {
    let h = measure(block(width: uk.width, isi)).height
    block(width: 100%, height: calc.ceil((h + 8pt) / 11pt - 0.001) * 11pt, above: 0pt, below: 0pt,
      breakable: false, align(horizon, isi))
  })
}
#let rumus-blok(it, lebar, nomor: none) = {
  let w = if nomor == none { 0pt } else { measure(text(11pt, nomor)).width + 6pt }
  let ruang = lebar - 2 * w
  let alami = measure(math.equation(math.display(it.body), block: false)).width
  if alami > ruang / 0.8 {
    let terlebar = potongan-terlebar(it)
    if terlebar != none and terlebar <= lebar - 12.5pt - 25pt - w {
      return rumus-baris(it, nomor)
    }
  }
  if nomor == none { return it }
  grid(columns: (w, 1fr, w), column-gutter: 0pt, align: (left + horizon, center + horizon, right + horizon),
    [], it, text(11pt, nomor))
}
#let rumus-nomor(nomor, isi) = layout(uk => context {
  let eq = isi-rumus(isi)
  if eq == none { isi } else { rumus-blok(eq, uk.width, nomor: nomor) }
})

// FIX-RUMUS-TEKS: persamaan yang diketik sebagai teks: tegak, rata kiri dengan indent menggantung,
// tidak diregangkan; jarak atas/bawah satu baris agar tetap di grid 11 pt.
#let rumus-teks(isi) = {
  v(11pt, weak: true)
  block(above: 0pt, below: 0pt, inset: (left: 12.5pt), {
    set par(justify: false, first-line-indent: 0pt, hanging-indent: 25pt)
    set text(style: "normal")
    isi
  })
  v(11pt, weak: true)
}

// =====================================================================
//  Fungsi utama
// =====================================================================
#let artikel(cfg, meta, body, header-grid: 0pt, footer-grid: 0pt) = {
  assert(header-grid in (-22pt, -11pt, 0pt, 11pt, 22pt), message: "Header harus kelipatan grid 11 pt")
  assert(footer-grid in (-22pt, -11pt, 0pt, 11pt, 22pt), message: "Footer harus kelipatan grid 11 pt")
  let hal-awal = int(meta.terbitan.halaman_awal)
  let info-terbitan(hal-akhir) = (cfg.singkatan_header + ", " + str(meta.terbitan.volume) + "("
    + str(meta.terbitan.nomor) + "), " + str(hal-awal) + "-" + str(hal-akhir) + ", "
    + meta.terbitan.edisi + ", " + str(meta.terbitan.tahun))

  set document(title: meta.judul, author: meta.penulis.map(p => p.nama))
  // FIX-FONT-CFG: font diambil dari config jurnal agar QA "unknown font" akurat
  set text(font: cfg.at("font", default: FONT), size: 11pt, fill: warna-teks, lang: "en",
    top-edge: tepi-atas, bottom-edge: tepi-bawah, hyphenate: cfg.at("hyphenate", default: true))
  show math.equation: set text(font: cfg.at("font_math", default: FONT-MATH))
  show math.equation.where(block: false): rumus-sebaris
  // FIX-GRID-RUMUS: rumus display menempati kelipatan tepat 11pt (min. 6pt jarak atas+bawah,
  // rumus di tengah) agar baris sesudahnya tetap sejajar dengan baris kolom sebelahnya.
  // FIX-RUMUS-LEBAR: rumus yang lebih lebar dari kolom diperkecil proporsional agar muat.
  show math.equation.where(block: true): it => context { if DI-TABEL.get() { align(start, box(math.equation(it.body, block: false))) } else { layout(ukuran => {
    let pecah = rumus-blok(it, ukuran.width)
    if pecah != it { return pecah }
    let lebar = measure(it).width
    let isi = if lebar > ukuran.width {
      scale(ukuran.width / lebar * 100%, reflow: true, it)
    } else { it }
    let h = measure(block(width: ukuran.width, isi)).height
    block(width: 100%, height: calc.ceil((h + 12pt) / 11pt - 0.001) * 11pt, above: 0pt,
      below: 0pt, breakable: false, align(horizon, isi))
  }) } }
  set par(justify: true, leading: 0pt, spacing: 0pt,
    first-line-indent: (amount: 25pt, all: true))
  set block(spacing: 0pt)
  set columns(gutter: JARAK-KOLOM)
  let di-tempat = cfg.at("penempatan_lebar", default: "mengambang") == "di-tempat"
  set columns(balanced: true) if di-tempat
  // FIX-BARIS-TUNGGAL: baris tunggal di puncak/dasar kolom dilarang (catatan redaksi 522);
  // kekurangan kolom kiri yang timbul diratakan oleh build.py (ratakan_kolom).
  set text(costs: (widow: 100%, orphan: 100%))
  MODE-DI-TEMPAT.update(di-tempat)
  set figure(gap: 0pt)
  set place(clearance: 11pt)
  show figure: set block(breakable: false)

  // =========================================================================
  // TAMBAHKAN BARIS INI TEPAT DI BAWAHNYA (SEKITAR BARIS 329):
  // Mengunci kata rujukan dan nomor ke dalam inline #box agar tidak terputus baris
  show regex("(?i)\b(Figure|Table|Fig\.|Tab\.|Equation|Eq\.|Gambar|Tabel)\s+\d+[\w.-]*"): box
  // =========================================================================
  
  // Pastikan elemen quote bawaan Typst diarahkan ke format wawancara (11pt)
  show quote.where(block: true): it => wawancara(it.body)
  
  // Tabel: garis atas & bawah tebal, garis tipis antar baris (gaya PDF lama)
  set table(stroke: (x, y) => (top: if y == 0 { 0.8pt } else { 0.3pt + warna-garis }),
    inset: (x: 4pt, y: 2.5pt), align: left + horizon)   // FIX-SEL-KIRI-TENGAH
  show table: it => {
    DI-TABEL.update(true)
    block(stroke: (bottom: 0.8pt), it)
    DI-TABEL.update(false)
  }

  // Judul bagian utama: ■ + teks kapital, menggantung 25pt
  // FIX-TAUTAN-OTOMATIS: setiap URL (http://, https://, www.), DOI ("doi: 10.xxx" / "doi:10.xxx")
  // dan alamat email di narasi maupun referensi menjadi hyperlink berwarna seperti halaman 1
  show link: set text(fill: warna-link)
  show regex("(?:https?://|www\.)[^\s<>\"]*[^\s<>\".,;:)\]]"): it => {
    let t = it.text
    link(if t.starts-with("www.") { "https://" + t } else { t }, it)
  }
  show regex("(?i)\bdoi:\s?10\.\d{4,9}/[^\s<>\"]*[^\s<>\".,;:)\]]"): it => {
    let t = it.text.replace(regex("(?i)^doi:\s?"), "")
    link("https://doi.org/" + t, it)
  }
  show regex("[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)*\.[A-Za-z]{2,}"): it => link("mailto:" + it.text, it)

  show par: it => {
    context { if _injeksi-boleh() { _titik-injeksi() } }
    it
  }

  show heading.where(level: 1): it => {
    context { if _injeksi-boleh() { _titik-injeksi() } }
    block(above: 0pt, below: 0pt, sticky: true, _tanpa-injeksi({
      set par(justify: false, first-line-indent: 0pt)
      set text(11pt, weight: "bold", fill: warna-bagian)
      grid(columns: (25pt, 1fr), "■", upper(it.body))
    }))
  }
  show heading.where(level: 2): it => {
    context { if _injeksi-boleh() { _titik-injeksi() } }
    block(above: 0pt, below: 0pt, sticky: true, _tanpa-injeksi({
      set par(justify: false, first-line-indent: 0pt)
      text(11pt, weight: "bold", it.body)
    }))
  }
  show heading.where(level: 3): it => {
    context { if _injeksi-boleh() { _titik-injeksi() } }
    block(above: 0pt, below: 0pt, sticky: true, _tanpa-injeksi({
      set par(justify: true, first-line-indent: 0pt)
      show emph: it => it.body      // FIX-SUBSUB-MIRING: teks miring dari Word tidak membalik jadi tegak
      text(11pt, weight: "regular", style: "italic", it.body)
    }))
  }
  // FIX-HEADING-L4: Heading 4 (sub-sub-sub bagian) tanpa spasi vertikal bawaan Typst; jarak
  // antarjudul bertingkat diatur sepenuhnya oleh #celah() di body.typ.
  show heading.where(level: 4): it => {
    context { if _injeksi-boleh() { _titik-injeksi() } }
    block(above: 0pt, below: 0pt, sticky: true, _tanpa-injeksi({
      set par(justify: true, first-line-indent: 0pt)
      text(11pt, weight: "regular", style: "normal", it.body)
    }))
  }

  set page(
    paper: "a4",
    margin: (left: KIRI, right: KIRI, top: MARGIN-ATAS, bottom: MARGIN-BAWAH),
    columns: if di-tempat { 1 } else { 2 },
    background: context {
      let i = here().page()
      if i == 1 {
        // FIX-KONVERGENSI-HALAMAN-AKHIR: rentang halaman dari meta (diisi build.py setelah
        // kompilasi pertama). counter(page).final() berayun selama float mengubah pagination
        // dan membuat Typst melaporkan non-convergence.
        let akhir = meta.at("_halaman_akhir", default: hal-awal)
        kepala-hal-1(cfg, info-terbitan(akhir))
      } else {
        // FIX-KONVERGENSI-HALAMAN: nomor cetak dihitung dari indeks halaman (here), bukan
        // counter(page). Counter di background ikut berubah setiap kali float mengubah
        // pagination, sehingga Typst bisa berayun dan gagal konvergen.
        let nomor = str(hal-awal + i - 1)
        let atas = if calc.odd(i) { meta.header_ganjil } else { cfg.nama_lengkap }
        let turun = calc.max(-22pt, calc.min(22pt, KEPALA-TURUN.final().at(str(i), default: 0pt)))
        place(top + left, dx: KIRI, dy: 89.9pt + turun, box(width: LEBAR-TEKS,
          align(center, text(9pt, style: "italic", atas))))
        place(top + left, dx: KIRI, dy: 101.1pt + turun, line(length: LEBAR-TEKS, stroke: 1pt + warna-garis))
        let naik = calc.max(-22pt, calc.min(22pt, footer-grid + KAKI-NAIK.final().at(str(i), default: 0pt)))
        place(top + left, dx: KIRI, dy: 765.5pt - naik, line(length: LEBAR-TEKS, stroke: 1pt + warna-garis))
        place(top + left, dx: KIRI, dy: 766.6pt - naik, box(width: LEBAR-TEKS, text(10pt,
          if calc.odd(i) { align(left, nomor + " | " + cfg.nama_lengkap) } else { align(right, cfg.nama_lengkap + " | " + nomor) })))
      }
    },
  )
  // ---- Blok halaman 1 (selebar halaman, mengambang di atas) ----
  let clr-hal1 = 21.5pt
  let d-abstrak = if header-grid == -22pt { 8.9pt } else if header-grid == -11pt { 10.9pt } else if header-grid == 11pt { 15.9pt } else if header-grid == 22pt { 18.9pt } else { 12.9pt }
  let v-sitasi = if header-grid == -22pt { 1.1pt } else if header-grid == -11pt { 3.1pt } else if header-grid == 11pt { 7.1pt } else if header-grid == 22pt { 9.1pt } else { 5.1pt }
  context {
    let base-top = if KEPALA-TURUN.final().values().any(v => v < 0pt) { 22pt } else { 0pt }
    let base-bot = if KAKI-NAIK.final().values().any(v => v < 0pt) { 22pt } else { 0pt }
    set page(margin: (left: KIRI, right: KIRI, top: MARGIN-ATAS - base-top, bottom: MARGIN-BAWAH - base-bot))
    counter(page).update(hal-awal)
    let kt1 = calc.max(-22pt, calc.min(22pt, KEPALA-TURUN.final().at("1", default: 0pt)))
    let kn1 = calc.max(-22pt, calc.min(22pt, KAKI-NAIK.final().at("1", default: 0pt)))
    let geser-atas1 = calc.max(-14pt, kt1)
    let clr1 = calc.max(0pt, clr-hal1 + (kt1 - geser-atas1))
    place(top, float: true, scope: "parent", clearance: clr1, block(width: 100%, {
      v(base-top + geser-atas1 - 0.4pt)
      blok-judul(meta, header-grid: header-grid)
      jarak(d-abstrak, 0pt, 10pt)
      grid(columns: (130pt, 1fr), column-gutter: 20pt, kolom-info(meta), kolom-abstrak(meta))
      v(v-sitasi)
      {
        let akhir = counter(page).final().first()
        let doi = ("https://doi.org/" + cfg.doi_prefix + "/v" + str(meta.terbitan.volume) + "i"
          + str(meta.terbitan.nomor) + ".pp" + str(hal-awal) + "-" + str(akhir))
        let sitasi = [#meta.sitasi_penulis (#str(meta.terbitan.tahun)). #meta.judul_kalimat. ] + emph(cfg.nama_lengkap) + [, ] + emph(str(meta.terbitan.volume)) + [(#str(meta.terbitan.nomor)), #str(hal-awal)\-#str(akhir).]
        blok-sitasi(meta, sitasi, doi, "https://creativecommons.org/licenses/by-sa/4.0/")
      }
    }))
    if base-bot + kn1 > 0pt {
      place(bottom, float: true, scope: "parent", clearance: 0pt, block(width: 100%, height: base-bot + kn1))
    }
    _HAL-INJEKSI-AKTIF.update(true)
    body
    context { if _injeksi-boleh() { _titik-injeksi() } }
  }
}
