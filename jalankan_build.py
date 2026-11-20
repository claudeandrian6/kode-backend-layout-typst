#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jalankan_build.py — peluncur build.py untuk Layout Editor.

Dipanggil oleh worker.py dari api.php melalui berkas job JSON. Skrip ini
menulis PID ke penanda .membangun, menjalankan build.py, menulis log, lalu
menghapus penanda. Tidak menerima perintah shell bebas.
"""
import json
import os
import re
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
# Samakan dengan worker: lingkungan minimal dan batas thread Typst/Rayon.
os.environ.setdefault("HOME", os.path.expanduser("~"))
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(os.environ["HOME"], ".cache"))
os.environ.setdefault("LANG", "C.UTF-8")
os.environ.setdefault("RAYON_NUM_THREADS", "2")
UNGGAH = os.path.join(BASE, "unggah")
PY = "/opt/typst-venv/bin/python3"


def di_unggah(jalur):
    try:
        nyata = os.path.realpath(jalur)
        dasar = os.path.realpath(UNGGAH)
    except OSError:
        return False
    return nyata.startswith(dasar + os.sep)


def main():
    if len(sys.argv) != 2:
        return 2
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            job = json.load(f)
    except (OSError, ValueError):
        return 2
    argv = job.get("argv")
    cwd = job.get("cwd")
    tanda = job.get("tanda", "")
    log = job.get("log", "")
    if not isinstance(argv, list) or len(argv) < 4:
        return 2
    if os.path.realpath(argv[0]) != os.path.realpath(PY):
        return 2
    awal = 2 if argv[1] == "-u" else 1
    if (len(argv) <= awal + 1
            or os.path.realpath(argv[awal]) != os.path.join(BASE, "build.py")):
        return 2
    docx = argv[awal + 1]
    if not di_unggah(docx) or not docx.lower().endswith(".docx"):
        return 2
    if "--jurnal" in argv:
        i = argv.index("--jurnal")
        if (i + 1 >= len(argv)
                or not re.match(r"^[A-Za-z0-9_\-]+$", argv[i + 1])
                or not os.path.isdir(os.path.join(BASE, "jurnal", argv[i + 1]))):
            return 2
    if any(a in ("--keluar", "--meta") for a in argv):
        return 2
    if not isinstance(cwd, str) or os.path.realpath(cwd) != BASE:
        return 2
    if not (di_unggah(tanda) and di_unggah(log)):
        return 2
    env = os.environ.copy()
    try:
        import worker
        env.update(worker.env_sah(job.get("env") or {}))
    except Exception:                                       # noqa: BLE001
        pass
    try:
        with open(tanda, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        with open(log, "a", encoding="utf-8") as f:
            f.write("[{}] build.py mulai (PID {})\n".format(
                time.strftime("%H:%M:%S"), os.getpid()))
        with open(log, "a", encoding="utf-8") as f:
            hasil = subprocess.run(argv, cwd=BASE, env=env,
                                   stdin=subprocess.DEVNULL,
                                   stdout=f, stderr=subprocess.STDOUT)
        with open(log, "a", encoding="utf-8") as f:
            f.write("== SELESAI (kode {})\n".format(hasil.returncode))
    except Exception as e:                                  # noqa: BLE001
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write("!! Gagal menjalankan build.py: {}\n".format(e))
        except OSError:
            pass
        return 3
    finally:
        try:
            os.unlink(tanda)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
