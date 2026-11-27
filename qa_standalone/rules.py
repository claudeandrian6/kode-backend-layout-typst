# -*- coding: utf-8 -*-
"""Definisi aturan tata letak jurnal, struktur Issue, dan QAReport mandiri."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


CATEGORY_LABELS: Dict[str, str] = {
    "kolom": "Keseimbangan Kolom",
    "orphan_heading": "Orphan Heading",
    "widow": "Widow (1 Baris di Puncak)",
    "orphan": "Orphan (1 Baris di Dasar)",
    "margin_objek": "Objek Menabrak Margin/Kolom",
    "margin_teks": "Teks Menabrak Margin/Footer",
    "rumus": "Format Rumus (Equation)",
    "tata_letak_objek": "Objek Berurutan / Bertumpuk",
    "jarak_sitasi": "Jarak Sitasi & Header",
    "grid": "Ketidakselarasan Grid 11 pt",
    "pita_teks": "Pita Teks Terjepit",
    "tipografi": "Font & Ukuran Teks",
}

SEVERITY_LABELS: Dict[str, str] = {
    "fatal": "FATAL",
    "error": "PELANGGARAN",
    "warning": "PERINGATAN",
}


@dataclass
class JournalRules:
    """Geometri halaman A4 dan aturan QA jurnal (IMRECS: BSSCD / PEDREV / RIETM)."""

    profile_name: str = "IMRECS Standar (A4, 2 Kolom)"
    page_width: float = 595.28
    page_height: float = 841.89
    margin_left: float = 76.535
    text_width: float = 442.2
    margin_right: float = 518.735
    col_width: float = 213.6
    gutter: float = 15.0
    col0_left: float = 76.535
    col0_right: float = 290.135
    col1_left: float = 305.135
    col1_right: float = 518.735
    gutter_center: float = 297.635
    margin_top: float = 102.4
    margin_bottom: float = 79.0
    content_bottom: float = 762.89
    grid_pt: float = 11.0
    baseline_phase: float = 111.2
    body_font_size: float = 11.0
    # Toleransi ketat untuk objek visual (gambar/tabel) vs longgar untuk teks justify
    margin_tol: float = 2.5
    gutter_tol: float = 3.0
    text_margin_tol: float = 6.5
    text_gutter_tol: float = 7.5
    # Keseimbangan kolom & ruang kosong
    right_gt_left_tol: float = 4.5
    left_hang_tol: float = 14.0
    right_short_tol: float = 14.5
    blank_below_obj_tol: float = 33.0
    empty_col_min_height: float = 150.0
    # Heading, grid, pita teks, rumus, objek berurutan, sitasi
    min_heading_lines: int = 2
    check_grid: bool = True
    grid_tol: float = 1.2
    min_ribbon_lines: int = 3
    check_equations: bool = True
    eq_indent_tol: float = 4.0
    eq_space_min_base: float = 18.0
    eq_space_min_box: float = 8.5
    check_cite_gap: bool = True
    cite_to_intro_pt: float = 22.0
    cite_to_intro_tol: float = 3.5
    max_consecutive_objects: int = 2
    # Standar tipografi (jenis & ukuran font)
    check_fonts: bool = True
    expected_font_family: str = "Times New Roman"
    table_font_size: float = 10.0
    table_caption_font_size: float = 10.0
    figure_caption_font_size: float = 10.0
    font_size_tol: float = 0.45

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Issue:
    """Satu temuan pelanggaran aturan tata letak pada halaman PDF."""

    page: int
    category: str
    severity: str
    title: str
    message: str
    suggestion: str
    bbox: Optional[Tuple[float, float, float, float]] = None
    column: Optional[int] = None

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def severity_label(self) -> str:
        return SEVERITY_LABELS.get(self.severity, self.severity.upper())

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["category_label"] = self.category_label
        data["severity_label"] = self.severity_label
        if data["bbox"] is not None:
            data["bbox"] = [round(v, 2) for v in data["bbox"]]
        return data


@dataclass
class QAReport:
    """Hasil pemeriksaan QA lengkap untuk satu berkas PDF."""

    file_path: str
    file_name: str
    page_count: int
    status: str
    issues: List[Issue] = field(default_factory=list)
    page_metrics: List[Dict[str, Any]] = field(default_factory=list)
    summary_by_category: Dict[str, int] = field(default_factory=dict)
    rules_used: Dict[str, Any] = field(default_factory=dict)

    @property
    def fatal_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "fatal")

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "file_name": self.file_name,
            "page_count": self.page_count,
            "status": self.status,
            "counts": {
                "total": len(self.issues),
                "fatal": self.fatal_count,
                "error": self.error_count,
                "warning": self.warning_count,
            },
            "summary_by_category": dict(self.summary_by_category),
            "rules_used": dict(self.rules_used),
            "page_metrics": list(self.page_metrics),
            "issues": [issue.to_dict() for issue in self.issues],
        }
