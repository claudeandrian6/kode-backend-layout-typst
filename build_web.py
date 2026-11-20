#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_web.py — pembungkus kompatibilitas untuk build.py produksi.

build.py dan template_jurnal.typ adalah berkas produksi yang tidak boleh diubah.
Pandoc dapat menghasilkan blok `#horizontalrule` (mis. garis pemisah di Word),
sedangkan template_jurnal.typ versi baru tidak lagi mendefinisikan nama tersebut.
Pembungkus ini menyisipkan definisi `horizontalrule` ke main.typ yang dibuat
build.py tepat sebelum setiap pemanggilan Typst, tanpa mengubah build.py maupun
template_jurnal.typ.

Semua argumen diteruskan apa adanya ke build.main().
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import build  # noqa: E402

# Definisi yang sama dengan template bawaan pandoc untuk blok HorizontalRule.
_SHIM = "#let horizontalrule = line(start: (25%, 0%), end: (75%, 0%))"

_jalankan_asli = build.jalankan


def _sisipkan_horizontalrule(main_path):
    """Tambahkan definisi ke body.typ bila body memakai #horizontalrule.

    ``include`` mengevaluasi body.typ di modulnya sendiri, sehingga definisi
    yang ditaruh di main.typ tidak terlihat oleh body.typ. body.typ sudah
    mengimpor template di baris pertamanya; definisi disisipkan setelah impor.
    """
    if not main_path or not os.path.isfile(main_path):
        return
    body_path = os.path.join(os.path.dirname(main_path), "body.typ")
    try:
        with open(body_path, encoding="utf-8") as f:
            isi = f.read()
        if "#horizontalrule" not in isi or "#let horizontalrule" in isi:
            return
        baris = isi.split("\n")
        for i, b in enumerate(baris):
            if b.lstrip().startswith("#import"):
                baris.insert(i + 1, _SHIM)
                break
        else:
            baris.insert(0, _SHIM)
        with open(body_path, "w", encoding="utf-8") as f:
            f.write("\n".join(baris))
    except OSError:
        # Biarkan build.py melaporkan error aslinya bila berkas tidak dapat dibaca.
        return


def _jalankan(cmd, **kw):
    try:
        if cmd and os.path.basename(str(cmd[0])).lower().startswith("typst"):
            for arg in cmd:
                if str(arg).endswith("main.typ"):
                    _sisipkan_horizontalrule(str(arg))
                    break
    except Exception:  # noqa: BLE001 - jangan menggagalkan build karena shim
        pass
    return _jalankan_asli(cmd, **kw)


build.jalankan = _jalankan

if __name__ == "__main__":
    build.main()
