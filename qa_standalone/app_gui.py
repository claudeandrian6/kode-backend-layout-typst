#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aplikasi Desktop Mandiri (GUI) Pemeriksa QA Layout PDF Jurnal."""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from qa_engine import analyze_pdf, export_html_report, render_annotated_page  # noqa: E402
from rules import CATEGORY_LABELS, Issue, JournalRules, QAReport  # noqa: E402

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    HAS_DND = True
except Exception:
    DND_FILES = None
    TkinterDnD = None
    HAS_DND = False

try:
    from PIL import ImageTk  # type: ignore
    HAS_IMAGETK = True
except Exception:
    ImageTk = None
    HAS_IMAGETK = False


def _pil_to_photoimage(pil_img) -> tk.PhotoImage:
    if HAS_IMAGETK and ImageTk is not None:
        return ImageTk.PhotoImage(pil_img)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return tk.PhotoImage(data=b64)


class QALayoutApp:
    """Jendela utama pemeriksa tata letak PDF mandiri untuk tim Scribus / PageMaker."""

    def __init__(self, root: tk.Tk, initial_pdf: Optional[str] = None):
        self.root = root
        self.root.title("QA Layout Jurnal — Pemeriksa PDF Mandiri (Scribus / PageMaker)")
        self.root.geometry("1260x820")
        self.root.minsize(960, 640)

        self.current_pdf: Optional[str] = None
        self.report: Optional[QAReport] = None
        self.current_page: int = 1
        self.zoom_dpi: int = 125
        self.highlight_issue_idx: Optional[int] = None
        self._photo_ref = None
        self._filtered_indices: List[int] = []

        self.var_min_heading = tk.IntVar(value=2)
        self.var_check_grid = tk.BooleanVar(value=True)
        self.var_show_guides = tk.BooleanVar(value=True)
        self.var_filter_cat = tk.StringVar(value="Semua Kategori")
        self.var_status_text = tk.StringVar(value="Pilih atau seret file PDF untuk mulai memeriksa.")

        self._setup_style()
        self._build_ui()

        if initial_pdf and os.path.isfile(initial_pdf):
            self.root.after(120, lambda: self.load_and_analyze(initial_pdf))

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background="#f1f5f9")
        style.configure("Card.TFrame", background="#ffffff", relief="flat")
        style.configure("Header.TLabel", background="#0f172a", foreground="#ffffff", font=("Segoe UI", 12, "bold"))
        style.configure("Sub.TLabel", background="#0f172a", foreground="#cbd5e1", font=("Segoe UI", 9))
        style.configure("Title.TLabel", background="#ffffff", foreground="#0f172a", font=("Segoe UI", 10, "bold"))

    def _build_ui(self) -> None:
        # 1. Header
        hdr = tk.Frame(self.root, bg="#0f172a", padx=14, pady=10)
        hdr.pack(fill="x")

        title_box = tk.Frame(hdr, bg="#0f172a")
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_box,
            text="QA Layout Jurnal — Pemeriksa PDF Mandiri",
            bg="#0f172a",
            fg="#ffffff",
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="Profil IMRECS A4 2 Kolom (76.5–518.7 pt | Gutter 15 pt | Grid 11 pt) — Scribus & PageMaker",
            bg="#0f172a",
            fg="#94a3b8",
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        self.lbl_badge = tk.Label(
            hdr,
            text="SIAP",
            bg="#334155",
            fg="#ffffff",
            font=("Segoe UI", 11, "bold"),
            padx=14,
            pady=6,
        )
        self.lbl_badge.pack(side="right", padx=(10, 0))

        # 2. Action Toolbar
        bar = tk.Frame(self.root, bg="#e2e8f0", padx=12, pady=7)
        bar.pack(fill="x")

        tk.Button(
            bar,
            text="Pilih File PDF...",
            command=self._choose_file,
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            relief="flat",
            padx=12,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        ).pack(side="left", padx=(0, 6))

        self.btn_recheck = tk.Button(
            bar,
            text="Periksa Ulang",
            command=self._recheck,
            state="disabled",
            relief="groove",
            padx=10,
            pady=4,
        )
        self.btn_recheck.pack(side="left", padx=4)

        self.btn_html = tk.Button(
            bar,
            text="Ekspor Laporan HTML",
            command=self._export_html,
            state="disabled",
            relief="groove",
            padx=10,
            pady=4,
        )
        self.btn_html.pack(side="left", padx=4)

        self.btn_json = tk.Button(
            bar,
            text="Ekspor JSON",
            command=self._export_json,
            state="disabled",
            relief="groove",
            padx=10,
            pady=4,
        )
        self.btn_json.pack(side="left", padx=4)

        tk.Label(bar, text="   Min. baris heading:", bg="#e2e8f0", font=("Segoe UI", 9)).pack(side="left", padx=(12, 4))
        sp = tk.Spinbox(bar, from_=2, to=3, width=3, textvariable=self.var_min_heading, command=self._recheck)
        sp.pack(side="left")

        tk.Checkbutton(
            bar,
            text="Cek Grid 11 pt",
            variable=self.var_check_grid,
            bg="#e2e8f0",
            activebackground="#e2e8f0",
            command=self._recheck,
        ).pack(side="left", padx=(10, 4))

        tk.Checkbutton(
            bar,
            text="Garis Panduan Margin/Kolom",
            variable=self.var_show_guides,
            bg="#e2e8f0",
            activebackground="#e2e8f0",
            command=self._render_current_page,
        ).pack(side="left", padx=4)

        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=120)
        self.progress.pack(side="right", padx=4)

        # 3. Split Main Panes
        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=8)

        # Left Pane: Visual Preview + Dropzone
        left_frame = tk.Frame(paned, bg="#ffffff", bd=1, relief="solid")
        paned.add(left_frame, weight=3)

        nav = tk.Frame(left_frame, bg="#f8fafc", padx=8, pady=6)
        nav.pack(fill="x")

        self.btn_prev = tk.Button(nav, text="< Sebelumnya", command=self._prev_page, state="disabled")
        self.btn_prev.pack(side="left")
        self.lbl_page = tk.Label(nav, text="Hal. 0 / 0", bg="#f8fafc", font=("Segoe UI", 9, "bold"), width=13)
        self.lbl_page.pack(side="left", padx=4)
        self.btn_next = tk.Button(nav, text="Berikutnya >", command=self._next_page, state="disabled")
        self.btn_next.pack(side="left")
        self.btn_next_bad = tk.Button(nav, text="Hal. Bermasalah >", command=self._next_issue_page, state="disabled")
        self.btn_next_bad.pack(side="left", padx=(8, 0))

        tk.Button(nav, text="+", width=3, command=lambda: self._zoom(20)).pack(side="right", padx=2)
        tk.Button(nav, text="Reset Zoom", command=lambda: self._set_zoom(125)).pack(side="right", padx=2)
        tk.Button(nav, text="-", width=3, command=lambda: self._zoom(-20)).pack(side="right", padx=2)

        canvas_wrap = tk.Frame(left_frame, bg="#cbd5e1")
        canvas_wrap.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_wrap, bg="#e2e8f0", highlightthickness=0)
        vscroll = ttk.Scrollbar(canvas_wrap, orient="vertical", command=self.canvas.yview)
        hscroll = ttk.Scrollbar(canvas_wrap, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)

        vscroll.pack(side="right", fill="y")
        hscroll.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.canvas.bind("<Configure>", lambda _e: self._draw_empty_state() if not self.report else None)
        self.canvas.bind("<Button-1>", lambda _e: self._choose_file() if not self.report else None)

        if HAS_DND and DND_FILES is not None:
            try:
                self.canvas.drop_target_register(DND_FILES)
                self.canvas.dnd_bind("<<Drop>>", self._on_drop_file)
            except Exception:
                pass

        # Right Pane: Summary + Issue List + Suggestion Detail
        right_frame = tk.Frame(paned, bg="#f8fafc", bd=1, relief="solid")
        paned.add(right_frame, weight=2)

        sum_box = tk.LabelFrame(right_frame, text="Ringkasan Pemeriksaan", bg="#ffffff", padx=8, pady=6)
        sum_box.pack(fill="x", padx=8, pady=(8, 6))

        self.cat_labels: Dict[str, tk.Label] = {}
        cats = list(CATEGORY_LABELS.items())
        for idx, (ckey, clabel) in enumerate(cats):
            r, c = divmod(idx, 2)
            cell = tk.Frame(sum_box, bg="#ffffff")
            cell.grid(row=r, column=c, sticky="ew", padx=4, pady=2)
            sum_box.grid_columnconfigure(c, weight=1)
            tk.Label(cell, text=f"{clabel}:", bg="#ffffff", fg="#334155", font=("Segoe UI", 8)).pack(side="left")
            lbl_val = tk.Label(cell, text="0", bg="#ffffff", fg="#0f172a", font=("Segoe UI", 9, "bold"))
            lbl_val.pack(side="right")
            self.cat_labels[ckey] = lbl_val

        flt_box = tk.Frame(right_frame, bg="#f8fafc", padx=8, pady=4)
        flt_box.pack(fill="x")
        tk.Label(flt_box, text="Filter Kategori:", bg="#f8fafc", font=("Segoe UI", 9)).pack(side="left")
        filter_opts = ["Semua Kategori"] + list(CATEGORY_LABELS.values())
        self.cmb_filter = ttk.Combobox(flt_box, textvariable=self.var_filter_cat, values=filter_opts, state="readonly")
        self.cmb_filter.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.cmb_filter.bind("<<ComboboxSelected>>", lambda _e: self._populate_tree())

        tree_wrap = tk.Frame(right_frame, bg="#ffffff")
        tree_wrap.pack(fill="both", expand=True, padx=8, pady=4)

        cols = ("no", "tingkat", "hal", "kategori", "judul")
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("no", text="No")
        self.tree.heading("tingkat", text="Tingkat")
        self.tree.heading("hal", text="Hal.")
        self.tree.heading("kategori", text="Kategori")
        self.tree.heading("judul", text="Masalah Terdeteksi")
        self.tree.column("no", width=36, anchor="center", stretch=False)
        self.tree.column("tingkat", width=92, anchor="center", stretch=False)
        self.tree.column("hal", width=54, anchor="center", stretch=False)
        self.tree.column("kategori", width=130, anchor="w", stretch=False)
        self.tree.column("judul", width=210, anchor="w", stretch=True)

        tsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_issue)

        det_box = tk.LabelFrame(right_frame, text="Detail & Saran Perbaikan (Scribus / PageMaker)", bg="#ffffff", padx=8, pady=6)
        det_box.pack(fill="x", padx=8, pady=(4, 8))
        self.txt_detail = tk.Text(det_box, height=6, wrap="word", font=("Segoe UI", 9), bg="#f8fafc", relief="flat")
        self.txt_detail.pack(fill="both", expand=True)
        self.txt_detail.configure(state="disabled")

        # 4. Status Bar
        sb = tk.Label(self.root, textvariable=self.var_status_text, anchor="w", bg="#e2e8f0", fg="#1e293b", padx=10, pady=4, font=("Segoe UI", 8))
        sb.pack(fill="x", side="bottom")

    def _draw_empty_state(self) -> None:
        self.canvas.delete("all")
        w = max(self.canvas.winfo_width(), 420)
        h = max(self.canvas.winfo_height(), 320)
        pad = 36
        self.canvas.create_rectangle(pad, pad, w - pad, h - pad, outline="#64748b", dash=(8, 6), width=2, fill="#f8fafc")
        dnd_msg = "Seret & Lepas (Drag & Drop) File PDF ke Sini\natau Klik untuk Memilih Berkas PDF" if HAS_DND else "Klik di Sini atau Tekan 'Pilih File PDF...' untuk Memeriksa PDF"
        self.canvas.create_text(
            w / 2,
            h / 2,
            text=f"{dnd_msg}\n\nPemeriksaan Lokal (Offline):\n• Keseimbangan Kolom, Kolom Kosong & Ruang Bawah Gambar\n• Orphan Heading (Min. 2-3 Baris) & Jarak Sitasi 22 pt\n• Widow, Orphan & Pita Teks Terjepit (< 3 Baris)\n• Format Rumus (Italic, No-Indent, Spasi 11 pt)\n• Gambar / Tabel Menabrak Margin, Gutter & Objek Berurutan (3+)",
            justify="center",
            fill="#334155",
            font=("Segoe UI", 11),
        )

    def _choose_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Pilih Berkas PDF Jurnal",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if path:
            self.load_and_analyze(path)

    def _on_drop_file(self, event) -> None:
        raw = (event.data or "").strip()
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1]
        elif " " in raw and not os.path.exists(raw):
            raw = raw.split()[0]
        if raw.lower().endswith(".pdf") and os.path.isfile(raw):
            self.load_and_analyze(raw)
        else:
            messagebox.showwarning("Format Tidak Didukung", "Harap jatuhkan berkas PDF (.pdf) yang valid.")

    def _recheck(self) -> None:
        if self.current_pdf and os.path.isfile(self.current_pdf):
            self.load_and_analyze(self.current_pdf)

    def load_and_analyze(self, pdf_path: str) -> None:
        self.current_pdf = os.path.abspath(pdf_path)
        self.highlight_issue_idx = None
        self.var_status_text.set(f"Sedang memeriksa {os.path.basename(pdf_path)}...")
        self.progress.start(12)
        self.btn_recheck.configure(state="disabled")
        self.btn_html.configure(state="disabled")
        self.btn_json.configure(state="disabled")

        rules = JournalRules(
            min_heading_lines=int(self.var_min_heading.get()),
            check_grid=bool(self.var_check_grid.get()),
        )

        def worker() -> None:
            try:
                rep = analyze_pdf(self.current_pdf, rules=rules)
                self.root.after(0, lambda: self._on_analysis_done(rep, None))
            except Exception as exc:
                self.root.after(0, lambda: self._on_analysis_done(None, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_analysis_done(self, report: Optional[QAReport], error_msg: Optional[str]) -> None:
        self.progress.stop()
        if error_msg or report is None:
            self.var_status_text.set("Gagal memeriksa PDF.")
            messagebox.showerror("Gagal Memeriksa PDF", f"Terjadi kesalahan:\n{error_msg}")
            return

        self.report = report
        self.current_page = report.issues[0].page if report.issues else 1
        self.highlight_issue_idx = 0 if report.issues else None

        self.btn_recheck.configure(state="normal")
        self.btn_html.configure(state="normal")
        self.btn_json.configure(state="normal")

        if report.status == "LULUS":
            self.lbl_badge.configure(text="LULUS QA", bg="#16a34a")
        else:
            self.lbl_badge.configure(text=f"PERLU PERBAIKAN ({len(report.issues)})", bg="#dc2626")

        for k, lbl in self.cat_labels.items():
            cnt = report.summary_by_category.get(k, 0)
            lbl.configure(text=str(cnt), fg="#dc2626" if cnt > 0 else "#16a34a")

        self._populate_tree()
        self._render_current_page()
        self.var_status_text.set(
            f"Selesai: {report.file_name} ({report.page_count} halaman) — "
            f"{report.fatal_count} fatal, {report.error_count} pelanggaran, {report.warning_count} peringatan."
        )

    def _populate_tree(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        self._filtered_indices = []
        if not self.report:
            return
        flt = self.var_filter_cat.get()
        for idx, iss in enumerate(self.report.issues):
            if flt != "Semua Kategori" and iss.category_label != flt:
                continue
            self._filtered_indices.append(idx)
            kol_str = f"Hal {iss.page}" + (" (Ki)" if iss.column == 0 else (" (Ka)" if iss.column == 1 else ""))
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(f"#{idx + 1}", iss.severity_label, kol_str, iss.category_label, iss.title),
            )
        if self._filtered_indices:
            first_iid = str(self._filtered_indices[0])
            self.tree.selection_set(first_iid)
            self._show_issue_detail(self.report.issues[self._filtered_indices[0]])
        else:
            self._set_detail_text("Tidak ada temuan pada filter kategori ini.")

    def _on_select_issue(self, _event) -> None:
        if not self.report:
            return
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        iss = self.report.issues[idx]
        self.highlight_issue_idx = idx
        self.current_page = iss.page
        self._show_issue_detail(iss)
        self._render_current_page()

    def _show_issue_detail(self, iss: Issue) -> None:
        kol = "Kiri" if iss.column == 0 else ("Kanan" if iss.column == 1 else "Penuh/Halaman")
        text = (
            f"[{iss.severity_label}] Halaman {iss.page} ({kol}) — {iss.category_label}\n"
            f"Masalah: {iss.message}\n\n"
            f"Saran Perbaikan: {iss.suggestion}"
        )
        self._set_detail_text(text)

    def _set_detail_text(self, text: str) -> None:
        self.txt_detail.configure(state="normal")
        self.txt_detail.delete("1.0", "end")
        self.txt_detail.insert("1.0", text)
        self.txt_detail.configure(state="disabled")

    def _render_current_page(self) -> None:
        if not self.report:
            self._draw_empty_state()
            return
        rules = JournalRules(**{k: v for k, v in self.report.rules_used.items() if k in JournalRules.__dataclass_fields__})
        img = render_annotated_page(
            self.report.file_path,
            self.current_page,
            self.report.issues,
            rules=rules,
            dpi=self.zoom_dpi,
            show_guides=bool(self.var_show_guides.get()),
            highlight_issue_idx=self.highlight_issue_idx,
        )
        self._photo_ref = _pil_to_photoimage(img)
        self.canvas.delete("all")
        self.canvas.create_image(10, 10, anchor="nw", image=self._photo_ref)
        self.canvas.configure(scrollregion=(0, 0, img.width + 20, img.height + 20))

        self.lbl_page.configure(text=f"Hal. {self.current_page} / {self.report.page_count}")
        self.btn_prev.configure(state="normal" if self.current_page > 1 else "disabled")
        self.btn_next.configure(state="normal" if self.current_page < self.report.page_count else "disabled")
        self.btn_next_bad.configure(state="normal" if any(True for _ in self.report.issues) else "disabled")

    def _prev_page(self) -> None:
        if self.report and self.current_page > 1:
            self.current_page -= 1
            self._render_current_page()

    def _next_page(self) -> None:
        if self.report and self.current_page < self.report.page_count:
            self.current_page += 1
            self._render_current_page()

    def _next_issue_page(self) -> None:
        if not self.report or not self.report.issues:
            return
        pages = sorted({i.page for i in self.report.issues})
        nxt = next((p for p in pages if p > self.current_page), pages[0])
        self.current_page = nxt
        self._render_current_page()

    def _zoom(self, delta: int) -> None:
        self._set_zoom(max(65, min(260, self.zoom_dpi + delta)))

    def _set_zoom(self, dpi: int) -> None:
        self.zoom_dpi = dpi
        if self.report:
            self._render_current_page()

    def _export_html(self) -> None:
        if not self.report:
            return
        default_name = os.path.splitext(self.report.file_name)[0] + "_QA_Report.html"
        path = filedialog.asksaveasfilename(
            title="Simpan Laporan QA Visual (HTML)",
            initialfile=default_name,
            defaultextension=".html",
            filetypes=[("HTML Report", "*.html")],
        )
        if path:
            out = export_html_report(self.report, path)
            messagebox.showinfo("Laporan Tersimpan", f"Laporan HTML berhasil disimpan ke:\n{out}")

    def _export_json(self) -> None:
        if not self.report:
            return
        default_name = os.path.splitext(self.report.file_name)[0] + "_QA_Report.json"
        path = filedialog.asksaveasfilename(
            title="Simpan Hasil QA (JSON)",
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON File", "*.json")],
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.report.to_dict(), f, ensure_ascii=False, indent=2)
            messagebox.showinfo("JSON Tersimpan", f"Hasil JSON berhasil disimpan ke:\n{path}")


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    initial_pdf = args[0] if args and os.path.isfile(args[0]) else None
    if HAS_DND and TkinterDnD is not None:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    QALayoutApp(root, initial_pdf=initial_pdf)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
