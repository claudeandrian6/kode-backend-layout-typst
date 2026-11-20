#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
impor.py — memasukkan hasil build.py dari komputer lokal (folder kerja/<nama> yang di-zip)
ke kerja/ di server, agar dapat disunting di Layout Editor.

Pemakaian:  python3 impor.py berkas.zip [--timpa]
Hasil: JSON di stdout {ok, nama, peringatan[]} atau {ok: false, galat}.

Zip boleh berisi folder <nama>/ (Compress-Archive kerja\\<nama>) atau langsung isi foldernya.
Nama artikel diambil dari jalur "/kerja/<nama>/" di body.typ (jalur gambar Typst bergantung
padanya), lalu dari nama folder di zip.
"""
import json
import os
import re
import shutil
import sys
import zipfile

BASIS = os.path.dirname(os.path.abspath(__file__))
KERJA = os.path.join(BASIS, "kerja")
NAMA_SAH = re.compile(r"^[A-Za-z0-9_\-]{1,120}$")
LEWATI = {"_skala.pdf", "_ukur.pdf", "_pita.pdf"}          # berkas ukur sementara build.py


def keluar(**data):
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(0)


def normal(nama):
    """Jalur entri zip yang aman (Windows memakai '\\'); None bila mencurigakan."""
    p = nama.replace("\\", "/").lstrip("/")
    bagian = [b for b in p.split("/") if b not in ("", ".")]
    if not bagian or any(b == ".." or ":" in b for b in bagian):
        return None
    return "/".join(bagian)


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit("pakai: impor.py berkas.zip [--timpa]")
    timpa = "--timpa" in argv
    try:
        zf = zipfile.ZipFile(argv[0])
    except (zipfile.BadZipFile, OSError):
        keluar(ok=False, galat="Berkas bukan zip yang sah")
    entri = {}
    for info in zf.infolist():
        if info.is_dir() or info.filename.endswith(("/", "\\")):
            continue
        p = normal(info.filename)
        if p:
            entri[p] = info
    if len(entri) > 5000:
        keluar(ok=False, galat="Zip berisi terlalu banyak berkas (maks. 5000)")
    batas_isi = 512 * 1024 * 1024
    if sum(max(0, info.file_size) for info in entri.values()) > batas_isi:
        keluar(ok=False, galat="Isi zip terlalu besar (maks. 512 MB)")
    bodies = sorted((p for p in entri if p == "body.typ" or p.endswith("/body.typ")), key=len)
    awalan = None
    for b in bodies:
        a = b[: -len("body.typ")]
        if a + "main.typ" in entri:
            awalan = a
            break
    if awalan is None:
        keluar(ok=False, galat="Zip tidak berisi main.typ dan body.typ (folder kerja\\<nama> hasil build.py)")

    body = zf.read(entri[awalan + "body.typ"]).decode("utf-8", "replace")
    main_typ = zf.read(entri[awalan + "main.typ"]).decode("utf-8", "replace")
    acuan = sorted(set(re.findall(r'"/kerja/([A-Za-z0-9_\-]+)/', body + main_typ)))
    if len(acuan) == 1:
        nama = acuan[0]
    else:
        nama = awalan.rstrip("/").split("/")[-1] if awalan else os.path.splitext(os.path.basename(argv[0]))[0]
    peringatan = []
    if len(acuan) > 1:
        peringatan.append("body.typ merujuk beberapa folder kerja: " + ", ".join(acuan))
    if not NAMA_SAH.match(nama):
        keluar(ok=False, galat="Nama artikel tidak sah: " + nama)

    m = re.search(r'json\("/jurnal/([^/"]+)/config\.json"\)', main_typ)
    if m and not os.path.isfile(os.path.join(BASIS, "jurnal", m.group(1), "config.json")):
        keluar(ok=False, galat="Jurnal '{}' belum ada di server (unggah folder jurnal\\{})".format(m.group(1), m.group(1)))

    tujuan = os.path.join(KERJA, nama)
    if os.path.exists(tujuan) and not timpa:
        keluar(ok=False, ada=True, nama=nama, galat="Artikel '{}' sudah ada di server".format(nama))

    sementara = tujuan + ".impor"
    shutil.rmtree(sementara, ignore_errors=True)
    os.makedirs(sementara)
    nulis = 0
    for p, info in entri.items():
        if not p.startswith(awalan):
            continue
        rel = p[len(awalan):]
        if os.path.basename(rel) in LEWATI or rel.startswith("_riwayat/"):
            continue
        dst = os.path.join(sementara, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with zf.open(info) as src, open(dst, "wb") as out:
            while True:
                b = src.read(1024 * 1024)
                if not b:
                    break
                nulis += len(b)
                if nulis > batas_isi:
                    out.close()
                    shutil.rmtree(sementara, ignore_errors=True)
                    keluar(ok=False, galat="Isi zip terlalu besar (maks. 512 MB)")
                out.write(b)

    # main.typ menunjuk meta.json lewat jalur akar; samakan dengan nama folder di server
    with open(os.path.join(sementara, "main.typ"), "w", encoding="utf-8") as f:
        f.write(re.sub(r'"/kerja/[^"]*/meta\.json"', '"/kerja/{}/meta.json"'.format(nama), main_typ))
    if not os.path.isfile(os.path.join(sementara, "meta.json")):
        peringatan.append("meta.json tidak ada di zip")

    if os.path.exists(tujuan):
        shutil.rmtree(tujuan)
    os.rename(sementara, tujuan)
    # Layout Editor: siapkan dokumen blok (id + style mikro) untuk artikel yang diimpor.
    try:
        import editor_blok
        editor_blok.muat_dokumen(tujuan)
    except Exception as e:  # noqa: BLE001
        peringatan.append("Dokumen blok editor belum dibuat: " + str(e))
    keluar(ok=True, nama=nama, peringatan=peringatan)


if __name__ == "__main__":
    main()
