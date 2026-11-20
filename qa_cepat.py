#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qa_cepat.py — mesin Layout Editor.

Untuk satu artikel di kerja/<nama>/: kompilasi main.typ -> hasil.pdf, render halaman ke PNG,
baca posisi objek & paragraf, lalu jalankan pemeriksaan aturan tata letak yang sama dengan
build.py. Hasilnya JSON di stdout (dibaca api.php).

Pemakaian:  python3 qa_cepat.py kerja/<nama> [--ppi 80] [--tanpa-gambar]
"""
import json
import os
import re
import sys

sys.argv_asli = list(sys.argv)
BASIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASIS)
sys.argv = [sys.argv[0]]
import build  # noqa: E402  (fungsi pemeriksaan dipakai ulang)


def baris_objek(body, nomor):
    """Baris #gambar/#tabel untuk objek bernomor `nomor` di body.typ."""
    m = re.search(r"^#(gambar|tabel|tabel-panjang)\([^\n]*?nomor: {}\b[^\n]*$".format(nomor),
                  body, flags=re.M)
    return m.group(0) if m else ""


def info_objek(panggil):
    lbl = re.search(r"label: \[([^\]]*)\]", panggil)
    kp = re.search(r'keputusan: "(\w+)"', panggil)
    sk = re.search(r"skala: ([\d.]+)", panggil)
    return dict(jenis="gambar" if panggil.startswith("#gambar") else "tabel",
                label=(lbl.group(1) if lbl else "").strip() or None,
                keputusan=kp.group(1) if kp else None,
                skala=float(sk.group(1)) if sk else 1.0,
                penuh=build.objek_penuh(panggil) if panggil else False)


def main():
    argv = sys.argv_asli[1:]
    if not argv:
        sys.exit("pakai: qa_cepat.py kerja/<nama> [--ppi 80] [--tanpa-gambar]")
    workdir = os.path.abspath(argv[0])
    ppi = int(argv[argv.index("--ppi") + 1]) if "--ppi" in argv else 80
    main_typ = os.path.join(workdir, "main.typ")
    body_path = os.path.join(workdir, "body.typ")
    pdf = os.path.join(workdir, "hasil.pdf")
    with open(body_path, encoding="utf-8") as f:
        body = f.read()
    hasil = {"ok": False}
    rc, out, err = build.jalankan([build.TYPST, "compile", "--root", build.BASE, "--font-path",
                                   os.path.join(build.BASE, "fonts"), main_typ, pdf])
    if rc != 0:
        hasil["galat"] = "\n".join(l for l in err.splitlines() if not l.startswith("warning"))[-3000:]
        print(json.dumps(hasil, ensure_ascii=False))
        return
    pos, galat = build.kueri_posisi(main_typ)
    if pos is None:
        hasil["galat"] = galat
        print(json.dumps(hasil, ensure_ascii=False))
        return
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open(pdf)
    n_hal = doc.page_count
    awal = 1
    try:
        with open(os.path.join(workdir, "meta.json"), encoding="utf-8") as f:
            awal = int(json.load(f)["terbitan"]["halaman_awal"])
    except Exception:
        pass
    # gambar halaman
    folder_png = os.path.join(workdir, "_pratinjau")
    os.makedirs(folder_png, exist_ok=True)
    if "--tanpa-gambar" not in argv:
        for f in os.listdir(folder_png):
            if f.endswith(".png"):
                os.remove(os.path.join(folder_png, f))
        for i, p in enumerate(doc, 1):
            p.get_pixmap(dpi=ppi).save(os.path.join(folder_png, "hal-{}.png".format(i)))
    # penanda referensi
    m = re.search(r"#referensi(?:\(kunci: (?:true|false)\))?\[#pm\((\d+)\);", body)
    build.REF_PM_AWAL[0] = int(m.group(1)) if m else None
    # objek
    objek = []
    penuh = {}
    for k, v in pos.items():
        mo = re.match(r"objek-info-(\d+)$", k)
        if not mo:
            continue
        n = int(mo.group(1))
        info = info_objek(baris_objek(body, n))
        penuh[n] = info["penuh"]
        tinggi = float(v[0][1]) if isinstance(v[0], (list, tuple)) else 0.0
        akhir = pos.get("objek-akhir-{}".format(n))
        if akhir and akhir[1] == v[1]:
            tinggi = max(tinggi, akhir[3] - v[3])
        objek.append(dict(info, nomor=n, hal=v[1], x=v[2], y=v[3], tinggi=tinggi or 60.0))
    objek.sort(key=lambda o: o["nomor"])
    # paragraf
    pindah = set(int(x) for x in re.findall(
        r"#colbreak\((?:weak: true)?\)(?:[ \t]*//[^\n]*)?\n\n?(?:#beku\([^)]*\)\[)?(?:=+ |#referensi(?:\([^)]*\))?\[|"
        r"#butir\([^)]*\)\[|#label-gantung\([^)]*\)\[)?#pm\((\d+)\);", body))
    paragraf = []
    for k, v in pos.items():
        if not k.startswith("pm-"):
            continue
        n = int(k[3:])
        ml = re.search(r"^.*?#pm\({}\);(.*)$".format(n), body, flags=re.M)
        cuplik = re.sub(r"#\w+\[|[\[\]\\]|#\w+\([^)]*\)", "", ml.group(1))[:70] if ml else ""
        paragraf.append(dict(n=n, hal=v[1], x=v[2], y=v[3], kolom=0 if v[2] < 300 else 1,
                             teks=cuplik, pindah_kolom=n in pindah,
                             referensi=bool(build.REF_PM_AWAL[0]) and n >= build.REF_PM_AWAL[0]))
    paragraf.sort(key=lambda p: p["n"])
    # footer naik
    kaki = {int(h): float(d) for h, d in re.findall(r"^#kaki-naik\((\d+), ([\d.]+)pt\)", body, flags=re.M)}
    # pemeriksaan aturan (sama dengan build.py)
    alignment = build.ukur_layout_ketat(pdf, pos, body)
    kolom = alignment["kolom"]
    masalah, catatan = [], []
    def hlm(h):
        return awal + h - 1
    for pesan in alignment["masalah"]:
        m = re.search(r"hlm fisik (\d+)", pesan)
        hal = int(m.group(1)) if m else 1
        item = dict(hal=hal, teks=pesan.replace(
            "hlm fisik {}".format(hal), "hlm {}".format(hlm(hal))))
        if "kolom kiri" in pesan.lower():
            item["kol"] = 0
        elif "kolom kanan" in pesan.lower():
            item["kol"] = 1
        masalah.append(item)
    for x in kolom:
        if x.get("pengecualian_target"):
            if x["kol"] == 0:
                kanan = next(y for y in kolom if y["hal"] == x["hal"] and y["kol"] == 1)
                catatan.append(dict(hal=x["hal"], teks=(
                    "Penyesuaian footer simetris hlm {}: baseline kiri {:.2f} = kanan {:.2f} pt; "
                    "footer naik {:.0f} pt").format(hlm(x["hal"]), x["akhir_baseline"],
                                                   kanan["akhir_baseline"], x["footer_naik"])))
        elif x.get("izin_akhir") and x["kurang"] > build.ALIGN_EPS and build.kolom_sah(x, n_hal):
            catatan.append(dict(hal=x["hal"], kol=1, teks=(
                "Halaman penutup: kolom kanan tersisa {:.0f} pt (maks. {:.0f} pt)").format(
                    x["kurang"], x["batas_kanan_pt"])))
    for pt in build.pita_kecil(pdf, pos, penuh, body):
        if pt["letak"] == "total":
            masalah.append(dict(hal=pt["hal"], teks="Hlm {}: hanya {} baris teks di sekitar objek (min. {})".format(
                hlm(pt["hal"]), pt["baris"], build.PITA_TOTAL_MIN)))
        else:
            batas_pita = getattr(build, "PITA_TENGAH_MIN", 7) if pt["letak"] == "tengah" else build.PITA_TEPI_MIN
            masalah.append(dict(hal=pt["hal"], teks="Hlm {}: pita teks {} baris {} objek (min. {})".format(
                hlm(pt["hal"]), pt["baris"], {"atas": "di atas", "bawah": "di bawah", "tengah": "di antara"}[pt["letak"]],
                batas_pita)))
    sudah_kol = {(m.get("hal"), m.get("kol")) for m in masalah if "kol" in m}
    for lb in build.lubang_kolom(pdf, pos, penuh, body):
        if (lb["hal"], lb["kol"]) in sudah_kol:
            continue
        sisi = "kiri" if lb["kol"] == 0 else "kanan"
        masalah.append(dict(hal=lb["hal"], kol=lb["kol"], teks=(
            "Lubang kolom {} hlm {} ({:.0f} pt lebih pendek dari kolom lain)".format(
                sisi, hlm(lb["hal"]), lb["selisih"]))))
    w, h = doc[0].rect.width, doc[0].rect.height
    hasil.update(ok=True, halaman=n_hal, halaman_awal=awal, lebar=w, tinggi=h, objek=objek,
                 paragraf=paragraf, kaki_naik=kaki, masalah=masalah, catatan=catatan,
                 kolom=[dict(hal=x["hal"], kol=x["kol"], kurang=round(x["kurang"], 1),
                             baseline_terakhir=round(x["akhir_baseline"], 3) if x["akhir_baseline"] is not None else None,
                             target_baseline=round(x["target"], 3),
                             baris_terlihat=x["baris_terlihat"]) for x in kolom],
                 peringatan_typst=[l for l in err.splitlines() if l.startswith("warning")][:20])
    hasil["status"] = "DITAHAN" if masalah else "LULUS"
    hasil["alignment"] = alignment
    doc.close()
    print(json.dumps(hasil, ensure_ascii=False))


if __name__ == "__main__":
    main()
