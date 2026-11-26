#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Antarmuka baris perintah (CLI) untuk QA PDF Jurnal Mandiri."""
from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from qa_engine import analyze_pdf, export_html_report  # noqa: E402
from rules import CATEGORY_LABELS, JournalRules  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pemeriksa QA Tata Letak PDF Jurnal Mandiri (Scribus / PageMaker)."
    )
    parser.add_argument("pdf_path", help="Jalur berkas PDF yang akan diperiksa.")
    parser.add_argument("--html", metavar="PATH", help="Simpan laporan visual HTML mandiri.")
    parser.add_argument("--json", metavar="PATH", help="Simpan hasil pemeriksaan ke JSON.")
    parser.add_argument(
        "--min-heading-lines",
        type=int,
        default=2,
        choices=(2, 3),
        help="Jumlah baris teks minimal di bawah subjudul sebelum batas kolom (default: 2).",
    )
    parser.add_argument(
        "--no-grid",
        action="store_true",
        help="Nonaktifkan pemeriksaan keselarasan grid 11 pt.",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not os.path.isfile(args.pdf_path):
        print(f"[GALAT] Berkas PDF tidak ditemukan: {args.pdf_path}", file=sys.stderr)
        return 2

    rules = JournalRules(
        min_heading_lines=args.min_heading_lines,
        check_grid=not args.no_grid,
    )
    try:
        report = analyze_pdf(args.pdf_path, rules=rules)
    except Exception as exc:
        print(f"[GALAT] Gagal memeriksa PDF: {exc}", file=sys.stderr)
        return 2

    print("=" * 72)
    print(f"Berkas  : {report.file_name} ({report.page_count} halaman)")
    print(f"Status  : {report.status}")
    print(
        f"Temuan  : {len(report.issues)} total "
        f"({report.fatal_count} fatal, {report.error_count} pelanggaran, {report.warning_count} peringatan)"
    )
    print("=" * 72)

    if report.issues:
        for idx, issue in enumerate(report.issues, 1):
            kol_str = (
                f", Kolom {'Kiri' if issue.column == 0 else 'Kanan'}"
                if issue.column in (0, 1)
                else ""
            )
            cat = CATEGORY_LABELS.get(issue.category, issue.category)
            print(f"{idx:2d}. [{issue.severity_label}] Hal. {issue.page}{kol_str} — {cat}: {issue.title}")
            print(f"    Detail : {issue.message}")
            print(f"    Saran  : {issue.suggestion}")
    else:
        print("Tidak ada pelanggaran tata letak yang terdeteksi.")

    if args.html:
        export_html_report(report, args.html)
        print(f"\nLaporan HTML tersimpan ke: {os.path.abspath(args.html)}")

    if args.json:
        out_dir = os.path.dirname(os.path.abspath(args.json))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"Laporan JSON tersimpan ke: {os.path.abspath(args.json)}")

    return 0 if report.status == "LULUS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
