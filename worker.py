#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
worker.py — eksekutor terbatas untuk Layout Editor.

Menerima permintaan JSON lewat Unix socket (worker.sock) dan hanya menjalankan
perintah yang sudah diizinkan: build.py, qa_cepat.py, impor.py, jalankan_build.py,
editor_blok.py, typst --version, dan pandoc --version.
Tidak memakai shell dan tidak menerima perintah bebas dari web.

Jalankan:  ./worker-start.sh
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
# Lingkungan minimal agar worker tetap benar walau dijalankan cron tanpa profil shell.
os.environ.setdefault("HOME", os.path.expanduser("~"))
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(os.environ["HOME"], ".cache"))
os.environ.setdefault("LANG", "C.UTF-8")
os.environ.setdefault("TYPST_BIN", os.path.join(BASE, "bin", "typst"))
os.environ.setdefault("PANDOC_BIN", os.path.join(BASE, "bin", "pandoc"))
# Batasi thread Typst/Rayon agar beberapa build/editor tidak menghabiskan batas thread host.
os.environ.setdefault("RAYON_NUM_THREADS", "2")
SOCK = os.environ.get("LE_WORKER_SOCK", os.path.join(BASE, "worker.sock"))
PIDFILE = os.path.join(BASE, "worker.pid")
LOG = os.path.join(BASE, "worker.log")
UNGGAH = os.path.join(BASE, "unggah")
KERJA = os.path.join(BASE, "kerja")
PY = "/opt/typst-venv/bin/python3"
TYPST = os.path.join(BASE, "bin", "typst")
PANDOC = os.path.join(BASE, "bin", "pandoc")
MAKS_KELUARAN = 2 * 1024 * 1024
IZIN_KERJA = threading.BoundedSemaphore(8)


def catat(pesan):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + pesan + "\n")
    except OSError:
        pass


def di_base(jalur):
    try:
        nyata = os.path.realpath(jalur)
    except OSError:
        return False
    return nyata == BASE or nyata.startswith(BASE + os.sep)


def di_unggah(jalur):
    try:
        return os.path.realpath(jalur).startswith(os.path.realpath(UNGGAH) + os.sep)
    except OSError:
        return False


def di_kerja(jalur):
    try:
        return os.path.realpath(jalur).startswith(os.path.realpath(KERJA) + os.sep)
    except OSError:
        return False


def argv_sah(argv, cwd):
    if not isinstance(argv, list) or not argv:
        return False
    if not all(isinstance(a, str) and "\x00" not in a for a in argv):
        return False
    if not isinstance(cwd, str) or not di_base(cwd):
        return False
    exe = os.path.realpath(argv[0])
    if exe == os.path.realpath(PY):
        if len(argv) < 2:
            return False
        awal = 1
        if argv[1] == "-u":
            awal = 2
        if len(argv) <= awal:
            return False
        skrip = os.path.realpath(argv[awal])
        if not di_base(skrip):
            return False
        nama = os.path.basename(skrip)
        sisa = argv[awal + 1:]
        if nama == "build.py":
            if not sisa or not di_unggah(sisa[0]) or not sisa[0].lower().endswith(".docx"):
                return False
            i = 1
            while i < len(sisa):
                a = sisa[i]
                if a in ("--verbose", "--tanpa-normalisasi"):
                    i += 1
                elif a in ("--jurnal", "--volume", "--nomor", "--tahun",
                           "--halaman-awal", "--batas-waktu") and i + 1 < len(sisa):
                    v = sisa[i + 1]
                    if a == "--jurnal":
                        if (not re.match(r"^[A-Za-z0-9_\-]+$", v)
                                or not os.path.isdir(os.path.join(BASE, "jurnal", v))):
                            return False
                    elif not v.isdigit():
                        return False
                    elif a == "--nomor" and v not in ("1", "2", "3", "4"):
                        return False
                    i += 2
                else:
                    return False                      # --keluar/--meta/opsi asing ditolak
            return all(o in sisa for o in ("--jurnal", "--volume", "--nomor",
                                           "--tahun", "--halaman-awal"))
        if nama == "qa_cepat.py":
            if not sisa or not di_kerja(sisa[0]) or not os.path.isdir(sisa[0]):
                return False
            i = 1
            while i < len(sisa):
                a = sisa[i]
                if a == "--tanpa-gambar":
                    i += 1
                elif (a == "--ppi" and i + 1 < len(sisa) and sisa[i + 1].isdigit()
                      and 20 <= int(sisa[i + 1]) <= 150):
                    i += 2
                else:
                    return False
            return True
        if nama == "impor.py":
            if not sisa or not di_unggah(sisa[0]) or not sisa[0].lower().endswith(".zip"):
                return False
            return all(a == "--timpa" for a in sisa[1:])
        if nama == "jalankan_build.py":
            return (len(sisa) == 1 and di_unggah(sisa[0])
                    and sisa[0].lower().endswith(".json"))
        if nama == "editor_blok.py":
            if len(sisa) < 2:
                return False
            aksi = sisa[0]
            if aksi not in ("get", "workspace", "qa", "update", "insert", "delete",
                            "batch", "move", "pindah", "save", "preview", "undo",
                            "redo", "auto_fix", "page", "migrate"):
                return False
            folder = sisa[1]
            if not di_kerja(folder) or not os.path.isdir(folder):
                return False
            if len(sisa) == 2:
                return True
            return (len(sisa) == 4 and sisa[2] == "--json"
                    and di_kerja(sisa[3]) and sisa[3].lower().endswith(".json")
                    and os.path.realpath(sisa[3]).startswith(
                        os.path.realpath(folder) + os.sep))
        return False
    if exe in (os.path.realpath(TYPST), os.path.realpath(PANDOC)):
        return len(argv) == 2 and argv[1] == "--version"
    return False


def env_sah(env):
    """Hanya variabel lingkungan yang dikenal dan nilainya tervalidasi."""
    if not isinstance(env, dict):
        return {}
    home = os.path.realpath(os.path.expanduser("~"))
    bin_base = os.path.realpath(os.path.join(BASE, "bin"))
    hasil = {}
    for k, v in env.items():
        if not isinstance(v, (str, int, float)):
            continue
        s = str(v)
        if k in ("TYPST_BIN", "PANDOC_BIN"):
            if not (di_base(s) and os.path.realpath(s).startswith(bin_base + os.sep)):
                continue
        elif k == "HOME":
            if os.path.realpath(s) != home:
                continue
        elif k == "XDG_CACHE_HOME":
            if not os.path.realpath(s).startswith(home + os.sep):
                continue
        elif k == "SOFFICE_BIN":
            if not (s.startswith(os.path.join(home, "domains") + os.sep)
                    and s.endswith("AppRun")):
                continue
        elif k in ("N_KANDIDAT", "RAYON_NUM_THREADS"):
            if not s.isdigit() or not 1 <= int(s) <= 64:
                continue
        elif k == "SKALA_PAKSA":
            if not re.match(r"^[\d.:,]+$", s):
                continue
        elif k == "LANG":
            if not re.match(r"^[A-Za-z0-9_.\-]+$", s):
                continue
        elif k == "PYTHONUNBUFFERED":
            if s not in ("0", "1"):
                continue
        hasil[k] = s
    return hasil


def balas(conn, data):
    try:
        conn.sendall((json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"))
    except OSError:
        pass


def proses(conn):
    if not IZIN_KERJA.acquire(blocking=False):
        balas(conn, {"kode": 429, "out": "", "err": "Worker sibuk"})
        try:
            conn.close()
        except OSError:
            pass
        return
    try:
        conn.settimeout(30)
        buf = b""
        while b"\n" not in buf:
            bagian = conn.recv(65536)
            if not bagian:
                return
            buf += bagian
            if len(buf) > 1024 * 1024:
                return balas(conn, {"kode": 400, "out": "", "err": "Permintaan terlalu besar"})
        try:
            req = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        except Exception:
            return balas(conn, {"kode": 400, "out": "", "err": "JSON tidak sah"})
        aksi = req.get("aksi")
        if aksi == "versi":
            hasil = {}
            for nama, argv in (
                ("python", [PY, "-c", "import sys,pymupdf,PIL;print(sys.version.split()[0],"
                                      "pymupdf.__version__,PIL.__version__)"]),
                ("typst", [TYPST, "--version"]),
                ("pandoc", [PANDOC, "--version"]),
            ):
                try:
                    r = subprocess.run(argv, cwd=BASE, stdin=subprocess.DEVNULL,
                                       capture_output=True, text=True, timeout=30)
                    hasil[nama] = (r.stdout or r.stderr).strip()[:500]
                except Exception as e:                      # noqa: BLE001
                    hasil[nama] = "galat: {}".format(e)
            return balas(conn, {"kode": 0, "out": json.dumps(hasil, ensure_ascii=False), "err": ""})
        if aksi not in ("jalan", "latar"):
            return balas(conn, {"kode": 400, "out": "", "err": "Aksi tidak dikenal"})
        argv = req.get("argv")
        cwd = req.get("cwd", BASE)
        if not argv_sah(argv, cwd):
            return balas(conn, {"kode": 403, "out": "", "err": "Perintah tidak diizinkan"})
        try:
            batas = max(1, min(900, int(req.get("timeout", 600))))
        except (TypeError, ValueError):
            batas = 600
        env = os.environ.copy()
        env.update(env_sah(req.get("env")))
        if aksi == "latar":
            try:
                p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
                return balas(conn, {"kode": 0, "out": json.dumps({"pid": p.pid}), "err": ""})
            except Exception as e:                          # noqa: BLE001
                return balas(conn, {"kode": 500, "out": "", "err": str(e)})
        try:
            r = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=batas)
            return balas(conn, {"kode": r.returncode,
                                "out": (r.stdout or "")[-MAKS_KELUARAN:],
                                "err": (r.stderr or "")[-MAKS_KELUARAN:]})
        except subprocess.TimeoutExpired:
            return balas(conn, {"kode": 124, "out": "", "err": "Batas waktu tercapai"})
        except Exception as e:                              # noqa: BLE001
            return balas(conn, {"kode": 500, "out": "", "err": str(e)})
    except Exception:                                       # noqa: BLE001
        catat("kesalahan koneksi:\n" + traceback.format_exc())
    finally:
        IZIN_KERJA.release()
        try:
            conn.close()
        except OSError:
            pass


def main():
    # Jika sudah ada worker hidup, keluar.
    if os.path.exists(SOCK):
        uji = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            uji.settimeout(1)
            uji.connect(SOCK)
            uji.close()
            print("worker sudah berjalan", file=sys.stderr)
            return
        except OSError:
            try:
                os.unlink(SOCK)
            except OSError:
                pass
        finally:
            try:
                uji.close()
            except OSError:
                pass
    os.umask(0o077)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o600)
    srv.listen(16)
    with open(PIDFILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    catat("worker siap (PID {})".format(os.getpid()))
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            continue
        threading.Thread(target=proses, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
