<?php
/**
 * normalisasi.php — menormalkan naskah Word (.docx) penulis sebelum diproses build.py/Typst.
 *
 * Penulis menulis naskah dengan berbagai kebiasaan (Shift+Enter, judul polos, judul & isi di satu
 * baris, track changes, komentar, kontrol konten, teks tersembunyi, page break, ...). Program ini
 * merapikan STRUKTUR naskah dan format keterangan tabel/gambar: label "Table N."/"Figure N."
 * tebal-regular, isi regular tidak miring, Sentence case (akronim dan nama diri dipertahankan),
 * agar setiap judul, paragraf, keterangan dan entri referensi berdiri sebagai paragraf Word
 * tersendiri dan build.py dapat langsung mengenalinya. Setiap perubahan dicatat di laporan.
 *
 * Pemakaian (CLI, PHP >= 7.4 dengan ekstensi zip, dom, mbstring):
 *   php normalisasi.php naskah.docx [hasil.docx] [--laporan laporan.json] [--diam]
 *   (tanpa hasil.docx -> naskah.normal.docx)
 * Sebagai pustaka (mis. dari api.php):
 *   require 'normalisasi.php';
 *   $lap = normalisasi_docx('naskah.docx', 'hasil.docx');   // ['ok'=>bool, 'perubahan'=>[...], ...]
 *
 * Bagian depan naskah (judul, penulis, afiliasi, abstrak, kata kunci) TIDAK diubah strukturnya
 * karena dibaca build.py dengan aturan tersendiri; normalisasi struktur dimulai dari judul bagian
 * utama pertama (INTRODUCTION/PENDAHULUAN). Isi tabel dan text box juga tidak dipecah.
 *
 * Daftar nama bagian di bawah sejalan dengan KATA_BAGIAN / RE_BAGIAN_AKHIR di build.py.
 */

const NS_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';
const NS_M = 'http://schemas.openxmlformats.org/officeDocument/2006/math';

const NORM_KATA_BAGIAN = [
    'INTRODUCTION', 'PENDAHULUAN', 'METHOD', 'METHODS', 'METODE', 'METODOLOGI', 'METHODOLOGY',
    'RESEARCH METHOD', 'RESEARCH METHODS', 'RESEARCH METHODOLOGY', 'METODE PENELITIAN',
    'MATERIALS AND METHODS', 'LITERATURE REVIEW', 'TINJAUAN PUSTAKA', 'KAJIAN PUSTAKA',
    'RESULT', 'RESULTS', 'HASIL', 'PEMBAHASAN', 'RESULT AND DISCUSSION', 'RESULTS AND DISCUSSION',
    'RESULTS AND DISCUSSIONS', 'HASIL DAN PEMBAHASAN', 'FINDINGS', 'FINDINGS AND DISCUSSION',
    'DISCUSSION', 'CONCLUSION', 'CONCLUSIONS', 'KESIMPULAN', 'SIMPULAN', 'PENUTUP',
    'CONCLUSION AND RECOMMENDATION', 'CONCLUSIONS AND RECOMMENDATIONS', 'KESIMPULAN DAN SARAN',
    'REFERENCES', 'REFERENCE', 'DAFTAR PUSTAKA', 'BIBLIOGRAPHY', 'DAFTAR RUJUKAN',
    'DECLARATION', 'ACKNOWLEDGEMENT', 'ACKNOWLEDGEMENTS', 'ACKNOWLEDGMENT', 'ACKNOWLEDGMENTS',
    'ACKNOWLEDGE', 'AUTHOR CONTRIBUTIONS', 'DECLARATION OF GENERATIVE AI USAGE IN THE WRITING PROCESS',
    "AUTHORS' DECLARATION OF AI USE",
    // lampiran sesudah REFERENCES
    'APPENDIX', 'APPENDICES', 'LAMPIRAN', 'ATTACHMENT', 'ATTACHMENTS', 'SUPPLEMENTARY MATERIAL',
    // variasi lain (Indonesia/Inggris) dan bahasa Melayu
    'BAHAN DAN METODE', 'TINJAUAN LITERATUR', 'LANDASAN TEORI', 'KAJIAN TEORI', 'THEORETICAL FRAMEWORK',
    'RESEARCH METHODS AND MATERIALS', 'METHODS AND MATERIALS', 'RESULTS AND ANALYSIS', 'ANALYSIS AND DISCUSSION',
    'PENGENALAN', 'KAEDAH', 'KAEDAH KAJIAN', 'METODOLOGI KAJIAN', 'DAPATAN', 'DAPATAN KAJIAN', 'PERBINCANGAN',
    'DAPATAN DAN PERBINCANGAN', 'RUJUKAN', 'PENGHARGAAN',
];
const NORM_LAMPIRAN = ['APPENDIX', 'APPENDICES', 'LAMPIRAN', 'ATTACHMENT', 'ATTACHMENTS', 'SUPPLEMENTARY MATERIAL'];
const NORM_AWAL = ['INTRODUCTION', 'PENDAHULUAN', 'PENGENALAN'];
const NORM_REFERENSI = ['REFERENCES', 'REFERENCE', 'DAFTAR PUSTAKA', 'BIBLIOGRAPHY', 'DAFTAR RUJUKAN', 'RUJUKAN'];
const NORM_PENUTUP = ['CONCLUSION', 'KESIMPULAN', 'PENUTUP', 'SIMPULAN', 'CLOSING', 'CONCLUDING',
                      'DISCUSSION', 'PEMBAHASAN'];
const NORM_RE_AKHIR = '/\b(DECLARATION|DECLARATIONS|DEKLARASI|PERNYATAAN|DISCLOSURE|STATEMENT|GENERATIVE|'
    . 'ARTIFICIAL INTELLIGENCE|AI|GENAI|ACKNOWLEDG\w*|UCAPAN TERIMA KASIH|TERIMA KASIH|FUNDING|'
    . 'PENDANAAN|CONFLICTS? OF INTEREST|COMPETING INTERESTS?|KONFLIK KEPENTINGAN|'
    . "AUTHORS?'? ?CONTRIBUTIONS?|CREDIT|KONTRIBUSI PENULIS|DATA AVAILABILITY|KETERSEDIAAN DATA|"
    . 'ETHICS?|ETHICAL|ETIKA|ETIK)\b/iu';
// PROFIL IDEAL (diambil dari naskah acuan 484 & 415): urutan bagian depan dan cara penulisannya.
const NORM_RE_INSTITUSI = '/univ|institut|college|school|facult|fakultas|department|departemen|jurusan|program|stud(?:y|i)|'
    . 'politekni|polytechnic|acad|akademi|sekolah|hospital|rumah sakit|ministry|kementerian|cent(?:er|re)|laborator|'
    . 'research|badan|lembaga|@|abstra|received|keywords?|kata kunci|corresponding|street|jalan|jl\./iu';
const NORM_URUT_BAGIAN = [
    'INTRODUCTION' => ['INTRODUCTION', 'PENDAHULUAN', 'PENGENALAN'],
    'METHOD' => ['METHOD', 'METHODS', 'METODE', 'METODOLOGI', 'METHODOLOGY', 'RESEARCH METHOD', 'RESEARCH METHODS',
                 'RESEARCH METHODOLOGY', 'METODE PENELITIAN', 'MATERIALS AND METHODS', 'BAHAN DAN METODE', 'KAEDAH',
                 'KAEDAH KAJIAN', 'METODOLOGI KAJIAN', 'RESEARCH METHODS AND MATERIALS', 'METHODS AND MATERIALS'],
    'RESULTS' => ['RESULT', 'RESULTS', 'HASIL', 'RESULT AND DISCUSSION', 'RESULTS AND DISCUSSION', 'RESULTS AND DISCUSSIONS',
                  'HASIL DAN PEMBAHASAN', 'FINDINGS', 'FINDINGS AND DISCUSSION', 'DAPATAN', 'DAPATAN KAJIAN',
                  'DAPATAN DAN PERBINCANGAN', 'RESULTS AND ANALYSIS', 'ANALYSIS AND DISCUSSION', 'DISCUSSION', 'PEMBAHASAN'],
    'CONCLUSION' => ['CONCLUSION', 'CONCLUSIONS', 'KESIMPULAN', 'SIMPULAN', 'PENUTUP', 'CONCLUSION AND RECOMMENDATION',
                     'CONCLUSIONS AND RECOMMENDATIONS', 'KESIMPULAN DAN SARAN'],
    'REFERENCES' => ['REFERENCES', 'REFERENCE', 'DAFTAR PUSTAKA', 'BIBLIOGRAPHY', 'DAFTAR RUJUKAN', 'RUJUKAN'],
];
const NORM_RE_LABEL = '/^((?:Figure|Fig\.?|Gambar|Table|Tabel)\s*\d+)\s*[\.:]?$/iu';
// Keterangan tabel/gambar: label + pemisah titik/titik dua + teks (boleh kosong).
const NORM_RE_KETERANGAN = '/^\s*((?:Table|Tabel|Figure|Fig\.?|Gambar)\s*(\d+))\s*[\.:]\s*(.*)$/iu';
// Kata yang tetap berkapital di tengah kalimat (nama diri/merek). Akronim dan kata
// berhuruf kapital internal (SFL, LREs, McDonald) sudah dipertahankan oleh algoritme.
const NORM_KATA_PANTAS = [
    'indonesia', 'indonesian', 'english', 'malay', 'malaysian', 'java', 'javanese', 'sundanese',
    'sulawesi', 'batangas', 'jakarta', 'halu', 'oleo', 'google', 'zoom', 'spss', 'ibm',
];
// Akronim yang tetap kapital saat seluruh caption ditulis kapital.
const NORM_AKRONIM = ['sfl', 'gl', 'lre', 'lres', 'tefl', 'efl', 'ai', 'genai', 'ict', 'spss',
                      'ibm', 'covid', 'cefr', 'esl', 'efl', 'toefl', 'ielts'];

// ----------------------------------------------------------------------------------------------
//  Teks
// ----------------------------------------------------------------------------------------------
function n_upper($s) { return function_exists('mb_strtoupper') ? mb_strtoupper($s, 'UTF-8') : strtoupper($s); }
function n_lower($s) { return function_exists('mb_strtolower') ? mb_strtolower($s, 'UTF-8') : strtolower($s); }

function n_rapi($t)
{
    $t = str_replace(["\xC2\xA0", "\xE2\x80\x8B", "\xEF\xBB\xBF", "\xE2\x81\xA0"], [' ', '', '', ''], $t);
    return trim(preg_replace('/\s+/u', ' ', $t));
}

/** Buang penomoran judul ("1.", "2.1", "IV.", "-", "•") seperti bersih_nomor() di build.py. */
function n_tanpa_nomor($t)
{
    // FIX-JUDUL-BULLET: judul Word sering diawali bullet (▪, ●, •, ...) + NBSP.
    return trim(preg_replace('/^[\s\x{00A0}]*(?:(?:\d+(?:\.\d+)*\.?|[IVXLC]+\.)[\s\x{00A0}]+|[\-•▪●○◦‣·■□➢][\s\x{00A0}]*)/u', '', $t));
}

function n_ada_huruf($t) { return (bool)preg_match('/\p{L}/u', $t); }

function n_kapital($t) { return n_ada_huruf($t) && $t === n_upper($t); }

/** Nama bagian baku (INTRODUCTION, REFERENCES, ...) — persis atau diawali nama itu. */
function n_bagian_baku($t, $daftar = NORM_KATA_BAGIAN)
{
    $u = n_upper(rtrim(n_tanpa_nomor(n_rapi($t)), ': '));
    if ($u === '' || mb_strlen($u) > 160) return '';
    foreach ($daftar as $k) {
        if ($u === $k) return $k;
    }
    // "CONCLUSION AND IMPLICATIONS", "REFERENCES:" — hanya bila ditulis kapital
    if (n_kapital(n_tanpa_nomor(n_rapi($t)))) {
        foreach ($daftar as $k) {
            if (strpos($u, $k . ' ') === 0) return $k;
        }
    }
    return '';
}

function n_awalan($t, $daftar)
{
    $u = n_upper(rtrim(n_tanpa_nomor(n_rapi($t)), ': '));
    foreach ($daftar as $k) {
        if (strpos($u, $k) === 0) return true;
    }
    return false;
}

/** Judul bagian penutup (deklarasi AI, ucapan terima kasih, pendanaan, ...) — sama dgn build.py. */
function n_judul_akhir($t)
{
    $t = rtrim(n_tanpa_nomor(n_rapi($t)), ': ');
    return $t !== '' && mb_strlen($t) <= 160 && !preg_match('/[\.;,]$/u', $t)
        && count(preg_split('/\s+/u', $t)) <= 20 && preg_match(NORM_RE_AKHIR, $t);
}

/** Baris pendek tanpa tanda baca akhir kalimat (kandidat judul). */
/** Daftar nama orang: tiap bagian (dipisah koma/;/&/and/dan) 1-7 kata berawalan kapital. */
function n_daftar_nama($t)
{
    $t = preg_replace('/[\d\*†‡¹²³⁴⁵⁶⁷⁸⁹⁰]+/u', ' ', $t);
    $bagian = array_filter(array_map('trim', preg_split('/\s*(?:,|;|&|\band\b|\bdan\b)\s*/u', $t)), 'strlen');
    if (!$bagian) return false;
    foreach ($bagian as $b) {
        $kata = preg_split('/\s+/u', $b);
        if (count($kata) < 1 || count($kata) > 7 || preg_match('/:$/u', $b)) return false;
        $besar = 0;
        foreach ($kata as $w) { if (preg_match('/^\p{Lu}/u', $w)) $besar++; }
        if ($besar < max(1, count($kata) - 1)) return false;
    }
    return true;
}

function n_baris_pendek($t, $maks_kata = 15)
{
    $t = n_rapi($t);
    return $t !== '' && n_ada_huruf($t) && mb_strlen($t) <= 160 && !preg_match('/[\.;,]$/u', $t)
        && count(preg_split('/\s+/u', $t)) <= $maks_kata;
}

/**
 * Sentence case keterangan tabel/gambar: kata pertama berkapital, kata lain huruf kecil.
 * Akronim (SFL, LREs), angka/rumus (Q1, COVID-19), kata berkapital internal (McDonald),
 * dan nama diri pada NORM_KATA_PANTAS dipertahankan. Kata ber-hyphen diproses per bagian.
 */
function n_kapital_kata($w)
{
    if ($w === '') return '';
    return n_upper(mb_substr($w, 0, 1)) . mb_substr($w, 1);
}

function n_sentence_token($w, $pertama, $paksa_kecil = false)
{
    if ($w === '') return '';
    $huruf = preg_replace('/[^\p{L}]/u', '', $w);
    $kecil = n_lower($w);
    if ($huruf !== '' && mb_strlen($huruf) >= 2 && $huruf === n_upper($huruf)) {
        if (in_array($kecil, NORM_KATA_PANTAS, true)) return n_kapital_kata($kecil);
        if ($paksa_kecil && !in_array($kecil, NORM_AKRONIM, true)) {
            return $pertama ? n_kapital_kata($kecil) : $kecil;
        }
        return $w;
    }
    if (preg_match('/\d/u', $w)) return $w;
    $kapital = preg_match_all('/\p{Lu}/u', $w);
    if ($kapital > 1 || ($kapital === 1 && !preg_match('/^\p{Lu}/u', $w))) return $w;
    if (in_array($kecil, NORM_KATA_PANTAS, true)) return n_kapital_kata($kecil);
    $w = $kecil;
    return $pertama ? n_kapital_kata($w) : $w;
}

function n_sentence_kata($w, $pertama, $paksa_kecil = false)
{
    $bagian = preg_split('/(-)/u', $w, -1, PREG_SPLIT_DELIM_CAPTURE);
    $hasil = ''; $awal = $pertama;
    foreach ($bagian as $b) {
        if ($b === '-') { $hasil .= '-'; $awal = false; continue; }
        $hasil .= n_sentence_token($b, $awal, $paksa_kecil);
        $awal = false;
    }
    return $hasil;
}

function n_sentence_case($t)
{
    $t = n_rapi($t);
    if ($t === '') return '';
    // Caption yang (hampir) seluruhnya kapital tidak membedakan akronim; hanya whitelist
    // akronim/nama diri yang dipertahankan. Rasio >= 80% menangani "SFL AND LREs ...".
    $huruf_semua = preg_replace('/[^\p{L}]/u', '', $t);
    $n_huruf = mb_strlen($huruf_semua);
    $n_kapital = preg_match_all('/\p{Lu}/u', $t);
    $paksa_kecil = $n_huruf >= 6 && ($n_kapital / max(1, $n_huruf)) >= 0.8;
    $bagian = preg_split('/(\s+)/u', $t, -1, PREG_SPLIT_DELIM_CAPTURE);
    $hasil = ''; $pertama = true;
    foreach ($bagian as $b) {
        if ($b === '') continue;
        if (preg_match('/^\s+$/u', $b)) { $hasil .= ' '; continue; }
        if (!preg_match('/\p{L}/u', $b)) { $hasil .= $b; continue; }
        $awal = ''; $inti = $b; $akhir = '';
        while ($inti !== '' && !preg_match('/^[\p{L}\p{N}]/u', $inti)) {
            $awal .= mb_substr($inti, 0, 1); $inti = mb_substr($inti, 1);
        }
        while ($inti !== '' && !preg_match('/[\p{L}\p{N}]$/u', $inti)) {
            $akhir = mb_substr($inti, -1) . $akhir; $inti = mb_substr($inti, 0, -1);
        }
        if ($inti === '') { $hasil .= $awal . $akhir; continue; }
        $hasil .= $awal . n_sentence_kata($inti, $pertama, $paksa_kecil) . $akhir;
        $pertama = false;
    }
    return trim($hasil);
}

// ----------------------------------------------------------------------------------------------
//  Normalisator
// ----------------------------------------------------------------------------------------------
class NormalisasiDocx
{
    /** @var DOMDocument */ private $doc;
    /** @var DOMXPath */ private $xp;
    private $gaya = [];          // styleId -> ['b'=>?bool, 'caps'=>?bool, 'induk'=>?string, 'judul'=>bool]
    private $gaya_baku_b = null; // docDefaults
    public $perubahan = [];
    private $lampiran = null;      // indeks paragraf judul lampiran sesudah REFERENCES (dari zona())
    public $peringatan = [];
    private $num_doc = null;       // word/numbering.xml (untuk daftar manual)
    private $num_xp = null;
    private $num_berubah = false;

    // ------------------------------------------------------------------ utilitas DOM
    private function w($nama) { return $this->doc->createElementNS(NS_W, 'w:' . $nama); }

    private function anak($el, $nama)
    {
        foreach ($el->childNodes as $c) {
            if ($c instanceof DOMElement && $c->namespaceURI === NS_W && $c->localName === $nama) return $c;
        }
        return null;
    }

    private function is_w($n, $nama) { return $n instanceof DOMElement && $n->namespaceURI === NS_W && $n->localName === $nama; }

    private function q($expr, $ctx = null) { return iterator_to_array($ctx ? $this->xp->query($expr, $ctx) : $this->xp->query($expr)); }

    private function buka(DOMElement $el)
    {
        $p = $el->parentNode;
        while ($el->firstChild) $p->insertBefore($el->firstChild, $el);
        $p->removeChild($el);
    }

    private function hapus($n) { if ($n->parentNode) $n->parentNode->removeChild($n); }

    private function catat($jenis, $teks = '')
    {
        $teks = n_rapi($teks);
        if (mb_strlen($teks) > 90) $teks = mb_substr($teks, 0, 87) . '...';
        $this->perubahan[] = ['jenis' => $jenis, 'teks' => $teks];
    }

    private static function nilai_on($el)
    {
        if (!$el) return null;
        $v = $el->getAttributeNS(NS_W, 'val');
        return !in_array(strtolower($v), ['0', 'false', 'off', 'none'], true);
    }

    // ------------------------------------------------------------------ gaya (styles.xml)
    public function muat_gaya($xml)
    {
        if (!$xml) return;
        $d = new DOMDocument();
        if (!@$d->loadXML($xml, LIBXML_NONET | LIBXML_PARSEHUGE)) return;
        $x = new DOMXPath($d);
        $x->registerNamespace('w', NS_W);
        foreach ($x->query('/w:styles/w:docDefaults/w:rPrDefault/w:rPr/w:b') as $b) $this->gaya_baku_b = self::nilai_on($b);
        foreach ($x->query('/w:styles/w:style') as $s) {
            $id = $s->getAttributeNS(NS_W, 'styleId');
            $nama = '';
            foreach ($x->query('w:name', $s) as $n) $nama = $n->getAttributeNS(NS_W, 'val');
            $induk = null;
            foreach ($x->query('w:basedOn', $s) as $n) $induk = $n->getAttributeNS(NS_W, 'val');
            $b = null; $caps = null; $lvl = null; $i_ = null;
            foreach ($x->query('w:rPr/w:b', $s) as $n) $b = self::nilai_on($n);
            foreach ($x->query('w:rPr/w:i', $s) as $n) $i_ = self::nilai_on($n);
            foreach ($x->query('w:rPr/w:caps', $s) as $n) $caps = self::nilai_on($n);
            foreach ($x->query('w:pPr/w:outlineLvl', $s) as $n) $lvl = (int)$n->getAttributeNS(NS_W, 'val');
            $this->gaya[$id] = ['b' => $b, 'caps' => $caps, 'i' => $i_, 'induk' => $induk,
                                'judul' => (bool)preg_match('/^(heading|judul)\s*\d/i', $nama) || ($lvl !== null && $lvl < 9)];
        }
    }

    // ------------------------------------------------------------------ numbering untuk daftar manual
    public function muat_numbering($xml)
    {
        if (!$xml) return;
        $d = new DOMDocument();
        $d->preserveWhiteSpace = true;
        if (!@$d->loadXML($xml, LIBXML_NONET | LIBXML_PARSEHUGE)) return;
        $this->num_doc = $d;
        $this->num_xp = new DOMXPath($d);
        $this->num_xp->registerNamespace('w', NS_W);
    }

    public function simpan_numbering()
    {
        return ($this->num_berubah && $this->num_doc !== null) ? $this->num_doc->saveXML() : null;
    }

    private function q_num($expr)
    {
        return iterator_to_array($this->num_xp->query($expr));
    }

    private function teks_mentah($p)
    {
        $t = '';
        foreach ($this->q('.//w:t[not(ancestor::w:txbxContent)]', $p) as $x) $t .= $x->nodeValue;
        return $t;
    }

    /** Deteksi penomoran manual di awal paragraf: a. / 1. / 1.2. / i. / (a) / (1). */
    private function deteksi_nomor_manual($raw)
    {
        $m = [];
        $paren = false;
        // FIX-LIST-NBSP: spasi setelah penanda bisa NBSP (Word menempelkan "a.\xA0").
        if (preg_match('/^[\s\x{00A0}]*\(([a-zA-Z0-9]+)\)[\s\x{00A0}]+/u', $raw, $m)) {
            $tok = $m[1]; $paren = true;
        } elseif (preg_match('/^[\s\x{00A0}]*(\d+(?:\.\d+)*)\.[\s\x{00A0}]+/u', $raw, $m)) {
            $tok = $m[1];
        } elseif (preg_match('/^[\s\x{00A0}]*([ivxIVX]+)\.[\s\x{00A0}]+/u', $raw, $m)) {
            $tok = $m[1];
        } elseif (preg_match('/^[\s\x{00A0}]*([a-zA-Z])\.[\s\x{00A0}]+/u', $raw, $m)) {
            $tok = $m[1];
        } else {
            return null;
        }
        // tahun di awal kalimat ("2019. ...") bukan daftar
        if (preg_match('/^(?:19|20)\d\d$/', $tok)) return null;
        if ($paren) {
            if (preg_match('/^\d+$/', $tok)) {
                return ['len' => mb_strlen($m[0], 'UTF-8'), 'fmt' => 'decimal', 'level' => 0,
                        'paren' => true, 'starts' => [max(1, (int)$tok)]];
            }
            if (preg_match('/^[ivxlcdmIVXLCDM]+$/', $tok)) {
                return ['len' => mb_strlen($m[0], 'UTF-8'),
                        'fmt' => preg_match('/^\p{Lu}/u', $tok) ? 'upperRoman' : 'lowerRoman',
                        'level' => 0, 'paren' => true, 'starts' => [self::nilai_romawi($tok)]];
            }
            return ['len' => mb_strlen($m[0], 'UTF-8'),
                    'fmt' => preg_match('/^\p{Lu}/u', $tok) ? 'upperLetter' : 'lowerLetter',
                    'level' => 0, 'paren' => true, 'starts' => [self::nilai_huruf($tok)]];
        }
        if (preg_match('/^\d/', $tok)) {
            $bag = array_map('intval', explode('.', $tok));
            return ['len' => mb_strlen($m[0], 'UTF-8'), 'fmt' => 'decimal',
                    'level' => max(0, min(8, count($bag) - 1)), 'paren' => false, 'starts' => $bag];
        }
        if (preg_match('/^[ivxIVX]+$/', $tok)) {
            return ['len' => mb_strlen($m[0], 'UTF-8'),
                    'fmt' => preg_match('/^\p{Lu}/u', $tok) ? 'upperRoman' : 'lowerRoman',
                    'level' => 0, 'paren' => false, 'starts' => [self::nilai_romawi($tok)]];
        }
        return ['len' => mb_strlen($m[0], 'UTF-8'),
                'fmt' => preg_match('/^\p{Lu}/u', $tok) ? 'upperLetter' : 'lowerLetter',
                'level' => 0, 'paren' => false, 'starts' => [self::nilai_huruf($tok)]];
    }

    private static function nilai_huruf($s)
    {
        $c = mb_substr($s, 0, 1, 'UTF-8');
        $o = function_exists('mb_ord') ? mb_ord($c, 'UTF-8') : ord($c);
        if ($o >= 97 && $o <= 122) return $o - 96;
        if ($o >= 65 && $o <= 90) return $o - 64;
        return 1;
    }

    private static function nilai_romawi($s)
    {
        $v = ['i' => 1, 'v' => 5, 'x' => 10, 'l' => 50, 'c' => 100, 'd' => 500, 'm' => 1000];
        $s = strtolower($s);
        $n = 0; $prev = 0;
        for ($i = strlen($s) - 1; $i >= 0; $i--) {
            $x = isset($v[$s[$i]]) ? $v[$s[$i]] : 0;
            if ($x < $prev) $n -= $x; else { $n += $x; $prev = $x; }
        }
        return max(1, $n);
    }

    private function lvl_text($l, $paren)
    {
        $p = [];
        for ($i = 0; $i <= $l; $i++) $p[] = '%' . ($i + 1);
        return $paren ? '(' . implode('.', $p) . ')' : implode('.', $p) . '.';
    }

    /** Buat abstractNum + num baru untuk format/level daftar manual; kembalikan numId. */
    private function buat_num($fmt, $level, $paren, $starts)
    {
        $aid = 0;
        foreach ($this->q_num('/w:numbering/w:abstractNum') as $a) {
            $aid = max($aid, (int)$a->getAttributeNS(NS_W, 'abstractNumId') + 1);
        }
        $nid = 0;
        foreach ($this->q_num('/w:numbering/w:num') as $n) {
            $nid = max($nid, (int)$n->getAttributeNS(NS_W, 'numId') + 1);
        }
        if ($aid < 1) $aid = 1;
        if ($nid < 1) $nid = 1;
        $abs = $this->num_doc->createElementNS(NS_W, 'w:abstractNum');
        $abs->setAttributeNS(NS_W, 'w:abstractNumId', (string)$aid);
        $mlt = $this->num_doc->createElementNS(NS_W, 'w:multiLevelType');
        $mlt->setAttributeNS(NS_W, 'w:val', 'hybridMultilevel');
        $abs->appendChild($mlt);
        for ($l = 0; $l <= 8; $l++) {
            $lvl = $this->num_doc->createElementNS(NS_W, 'w:lvl');
            $lvl->setAttributeNS(NS_W, 'w:ilvl', (string)$l);
            $st = isset($starts[$l]) ? max(1, (int)$starts[$l]) : 1;
            $e = $this->num_doc->createElementNS(NS_W, 'w:start');
            $e->setAttributeNS(NS_W, 'w:val', (string)$st); $lvl->appendChild($e);
            $e = $this->num_doc->createElementNS(NS_W, 'w:numFmt');
            $e->setAttributeNS(NS_W, 'w:val', $fmt); $lvl->appendChild($e);
            $e = $this->num_doc->createElementNS(NS_W, 'w:lvlText');
            $e->setAttributeNS(NS_W, 'w:val', $this->lvl_text($l, $paren)); $lvl->appendChild($e);
            $e = $this->num_doc->createElementNS(NS_W, 'w:lvlJc');
            $e->setAttributeNS(NS_W, 'w:val', 'left'); $lvl->appendChild($e);
            $ppr = $this->num_doc->createElementNS(NS_W, 'w:pPr');
            $ind = $this->num_doc->createElementNS(NS_W, 'w:ind');
            $ind->setAttributeNS(NS_W, 'w:left', (string)(720 + $l * 360));
            $ind->setAttributeNS(NS_W, 'w:hanging', '360');
            $ppr->appendChild($ind); $lvl->appendChild($ppr);
            $abs->appendChild($lvl);
        }
        $root = $this->q_num('/w:numbering')[0];
        $first_num = null;
        foreach ($root->childNodes as $c) {
            if ($c instanceof DOMElement && $c->localName === 'num') { $first_num = $c; break; }
        }
        if ($first_num) $root->insertBefore($abs, $first_num); else $root->appendChild($abs);
        $num = $this->num_doc->createElementNS(NS_W, 'w:num');
        $num->setAttributeNS(NS_W, 'w:numId', (string)$nid);
        $an = $this->num_doc->createElementNS(NS_W, 'w:abstractNumId');
        $an->setAttributeNS(NS_W, 'w:val', (string)$aid);
        $num->appendChild($an);
        $root->appendChild($num);
        $this->num_berubah = true;
        return $nid;
    }

    private function hapus_awalan($p, $n)
    {
        if ($n <= 0) return;
        foreach ($this->q('.//w:t[not(ancestor::w:txbxContent)]', $p) as $t) {
            $s = $t->nodeValue;
            $l = mb_strlen($s, 'UTF-8');
            if ($n >= $l) {
                $t->nodeValue = '';
                $n -= $l;
            } else {
                $t->nodeValue = mb_substr($s, $n, $l - $n, 'UTF-8');
                $n = 0;
            }
            if ($n <= 0) break;
        }
    }

    private function pasang_numpr($p, $numid, $level)
    {
        $ppr = $this->anak($p, 'pPr');
        if (!$ppr) {
            $ppr = $this->w('pPr');
            $p->insertBefore($ppr, $p->firstChild);
        }
        $np = $this->w('numPr');
        $e = $this->w('ilvl'); $e->setAttributeNS(NS_W, 'w:val', (string)$level); $np->appendChild($e);
        $e = $this->w('numId'); $e->setAttributeNS(NS_W, 'w:val', (string)$numid); $np->appendChild($e);
        // FIX-INDENT-LIST-MANUAL: buang indentasi Word asli dan paksa indent nol; indentasi
        // daftar cukup dari grid #butir di template (15 pt), jangan menumpuk.
        $ind_lama = $this->anak($ppr, 'ind');
        if ($ind_lama) $this->hapus($ind_lama);
        $ind = $this->w('ind');
        $ind->setAttributeNS(NS_W, 'w:left', '0');
        $ind->setAttributeNS(NS_W, 'w:firstLine', '0');
        $ind->setAttributeNS(NS_W, 'w:hanging', '0');
        $sebelum = null;
        foreach ($ppr->childNodes as $c) {
            if (!$c instanceof DOMElement) continue;
            if (!in_array($c->localName,
                    ['pStyle', 'keepNext', 'keepLines', 'pageBreakBefore', 'framePr', 'widowControl'], true)) {
                $sebelum = $c; break;
            }
        }
        if ($sebelum) $ppr->insertBefore($np, $sebelum); else $ppr->appendChild($np);
        // w:ind harus sesudah pBdr/spacing dan sebelum jc/rPr
        $sebelum = null;
        foreach ($ppr->childNodes as $c) {
            if (!$c instanceof DOMElement) continue;
            if (in_array($c->localName,
                    ['contextualSpacing', 'mirrorIndents', 'suppressOverlap', 'jc', 'textDirection',
                     'textAlignment', 'textboxTightWrap', 'outlineLvl', 'divId', 'cnfStyle',
                     'rPr', 'sectPr', 'pPrChange'], true)) {
                $sebelum = $c; break;
            }
        }
        if ($sebelum) $ppr->insertBefore($ind, $sebelum); else $ppr->appendChild($ind);
    }

    /**
     * Ubah penomoran manual di awal paragraf isi menjadi daftar Word (w:numPr).
     * Judul bagian, keterangan, catatan, dan zona referensi tidak disentuh.
     */
    private function normalkan_daftar_manual($pars, $awal, $ref)
    {
        if ($awal === null) return;
        $akhir = $ref === null ? count($pars) : $ref;
        if ($this->num_doc === null) {
            for ($i = $awal; $i < $akhir; $i++) {
                $raw = $this->teks_mentah($pars[$i]);
                if ($raw !== '' && $this->deteksi_nomor_manual($raw) !== null) {
                    $this->peringatan[] = 'Penomoran manual ditemukan tetapi word/numbering.xml '
                        . 'tidak tersedia; daftar tidak diubah.';
                    break;
                }
            }
            return;
        }
        $num_id = null; $prev_i = null; $fmt = null; $level = null; $paren = null;
        for ($i = $awal; $i < $akhir; $i++) {
            $p = $pars[$i];
            $raw = $this->teks_mentah($p);
            if ($raw === '') { $num_id = null; $prev_i = null; continue; }
            $t = n_rapi($raw);
            $ppr = $this->anak($p, 'pPr');
            if (($ppr && $this->anak($ppr, 'numPr')) || $this->par_judul_gaya($p)
                    || $this->q('.//w:drawing | .//w:pict | .//m:oMath | .//w:txbxContent', $p)
                    || n_bagian_baku($t) !== ''
                    || preg_match(NORM_RE_LABEL, $t)
                    || preg_match('/^(Notes?|Source|Sumber|Catatan)\b/iu', $t)) {
                $num_id = null; $prev_i = null; continue;
            }
            $det = $this->deteksi_nomor_manual($raw);
            if ($det === null) { $num_id = null; $prev_i = null; continue; }
            if ($num_id === null || $prev_i !== $i - 1 || $fmt !== $det['fmt']
                    || $level !== $det['level'] || $paren !== $det['paren']) {
                $num_id = $this->buat_num($det['fmt'], $det['level'], $det['paren'], $det['starts']);
                $fmt = $det['fmt']; $level = $det['level']; $paren = $det['paren'];
            }
            $this->hapus_awalan($p, $det['len']);
            $this->pasang_numpr($p, $num_id, $det['level']);
            $this->catat('penomoran manual dijadikan daftar', $t);
            $prev_i = $i;
        }
    }

    private function sifat_gaya($id, $kunci, $dalam = 0)
    {
        if ($id === null || $id === '' || !isset($this->gaya[$id]) || $dalam > 20) return null;
        $g = $this->gaya[$id];
        if ($kunci === 'judul' ? $g['judul'] : $g[$kunci] !== null) return $g[$kunci];
        return $this->sifat_gaya($g['induk'], $kunci, $dalam + 1);
    }

    private function gaya_par($p)
    {
        $ppr = $this->anak($p, 'pPr');
        $ps = $ppr ? $this->anak($ppr, 'pStyle') : null;
        return $ps ? $ps->getAttributeNS(NS_W, 'val') : null;
    }

    private function par_judul_gaya($p)
    {
        $ppr = $this->anak($p, 'pPr');
        if ($ppr && ($o = $this->anak($ppr, 'outlineLvl')) && (int)$o->getAttributeNS(NS_W, 'val') < 9) return true;
        return (bool)$this->sifat_gaya($this->gaya_par($p), 'judul');
    }

    private function run_sifat($r, $kunci, $gaya_p)
    {
        $rpr = $this->anak($r, 'rPr');
        if ($rpr && ($e = $this->anak($rpr, $kunci))) return self::nilai_on($e);
        if ($rpr && ($rs = $this->anak($rpr, 'rStyle'))) {
            $v = $this->sifat_gaya($rs->getAttributeNS(NS_W, 'val'), $kunci);
            if ($v !== null) return $v;
        }
        $v = $this->sifat_gaya($gaya_p, $kunci);
        if ($v !== null) return $v;
        return $kunci === 'b' ? (bool)$this->gaya_baku_b : false;
    }

    // ------------------------------------------------------------------ tahap 1: pembersihan
    public function bersihkan(DOMDocument $doc, $bagian)
    {
        $this->doc = $doc;
        $this->xp = new DOMXPath($doc);
        $this->xp->registerNamespace('w', NS_W);
        $this->xp->registerNamespace('m', NS_M);
        $n = 0;
        // Track changes diterima: sisipan dipertahankan, hapusan dibuang
        foreach ($this->q('//w:del[not(parent::w:rPr)] | //w:moveFrom[not(parent::w:rPr)]') as $e) { $this->hapus($e); $n++; }
        foreach ($this->q('//w:tr[w:trPr/w:del]') as $e) { $this->hapus($e); $n++; }
        foreach ($this->q('//w:ins[not(parent::w:rPr) and not(parent::w:trPr)] | //w:moveTo[not(parent::w:rPr)]') as $e) { $this->buka($e); $n++; }
        foreach ($this->q('//w:rPr/w:ins | //w:rPr/w:del | //w:rPr/w:moveFrom | //w:rPr/w:moveTo | //w:trPr/w:ins'
            . ' | //w:moveFromRangeStart | //w:moveFromRangeEnd | //w:moveToRangeStart | //w:moveToRangeEnd'
            . ' | //w:rPrChange | //w:pPrChange | //w:sectPrChange | //w:tblPrChange | //w:tblPrExChange'
            . ' | //w:trPrChange | //w:tcPrChange | //w:tblGridChange | //w:numberingChange | //w:cellIns | //w:cellDel') as $e) {
            $this->hapus($e); $n++;
        }
        if ($n) $this->catat('track changes diterima' . " ($bagian)", "$n penanda");
        // Komentar
        $n = 0;
        foreach ($this->q('//w:commentRangeStart | //w:commentRangeEnd | //w:commentReference') as $e) {
            $r = $e->parentNode;
            $this->hapus($e);
            if ($this->is_w($r, 'r') && !$this->q('*[not(self::w:rPr)]', $r)) $this->hapus($r);
            $n++;
        }
        if ($n) $this->catat('komentar dibuang' . " ($bagian)", "$n penanda");
        // Kontrol konten, smart tag, customXml: isinya dipertahankan
        $n = 0;
        foreach ($this->q('//w:sdt') as $e) {
            $isi = $this->anak($e, 'sdtContent');
            if ($isi) { $this->buka($isi); }
            foreach (['sdtPr', 'sdtEndPr'] as $k) { if ($x = $this->anak($e, $k)) $this->hapus($x); }
            $this->buka($e);
            $n++;
        }
        foreach ($this->q('//w:smartTag | //w:customXml') as $e) {
            foreach (['smartTagPr', 'customXmlPr'] as $k) { if ($x = $this->anak($e, $k)) $this->hapus($x); }
            $this->buka($e); $n++;
        }
        if ($n) $this->catat('kontrol konten/smart tag dibuka' . " ($bagian)", "$n elemen");
        // Teks tersembunyi (bukan tanda paragraf)
        $n = 0;
        foreach ($this->q('//w:r[w:rPr/w:vanish]') as $r) {
            if (self::nilai_on($this->anak($this->anak($r, 'rPr'), 'vanish')) && !$this->q('.//w:fldChar | .//w:instrText', $r)) {
                $this->catat('teks tersembunyi dibuang', $r->textContent);
                $this->hapus($r); $n++;
            }
        }
        // Sisa penanda yang tidak berarti untuk tata letak
        foreach ($this->q('//w:proofErr | //w:lastRenderedPageBreak | //w:pPr/w:pageBreakBefore') as $e) $this->hapus($e);
        // Karakter tak terlihat
        $n = 0;
        foreach ($this->q('//w:t | //m:t') as $t) {
            $baru = str_replace(["\xE2\x80\x8B", "\xEF\xBB\xBF", "\xE2\x81\xA0"], '', $t->nodeValue);
            if ($baru !== $t->nodeValue) { $t->nodeValue = $baru; $n++; }
        }
        if ($n) $this->catat('karakter tak terlihat dibuang' . " ($bagian)", "$n potong teks");
    }

    // ------------------------------------------------------------------ segmen paragraf
    /** Pecah run berisi br + konten lain menjadi run-run terpisah; page/column break -> br biasa. */
    private function pisah_run_br($p)
    {
        foreach ($this->q('w:r[w:br or w:cr]', $p) as $r) {
            foreach ($this->q('w:br[@w:type="page" or @w:type="column"]', $r) as $br) {
                $br->removeAttributeNS(NS_W, 'type');
            }
            foreach ($this->q('w:cr', $r) as $cr) {
                $r->replaceChild($this->w('br'), $cr);
            }
            $isi = [];
            foreach ($r->childNodes as $c) {
                if ($c instanceof DOMElement && !$this->is_w($c, 'rPr')) $isi[] = $c;
            }
            if (count($isi) <= 1) continue;
            $rpr = $this->anak($r, 'rPr');
            $grup = [];
            foreach ($isi as $c) {
                if ($this->is_w($c, 'br')) { $grup[] = [$c]; $grup[] = []; }
                else { if (!$grup) $grup[] = []; $grup[count($grup) - 1][] = $c; }
            }
            foreach ($grup as $g) {
                if (!$g) continue;
                $baru = $this->w('r');
                if ($rpr) $baru->appendChild($rpr->cloneNode(true));
                foreach ($g as $c) $baru->appendChild($c);
                $p->insertBefore($baru, $r);
            }
            $this->hapus($r);
        }
    }

    private function run_br($n)
    {
        if (!$this->is_w($n, 'r')) return false;
        $ada = false;
        foreach ($n->childNodes as $c) {
            if (!$c instanceof DOMElement || $this->is_w($c, 'rPr')) continue;
            if ($this->is_w($c, 'br') && $c->getAttributeNS(NS_W, 'type') === '') { $ada = true; continue; }
            return false;
        }
        return $ada;
    }

    /** Segmen paragraf yang dipisah br di tingkat paragraf (bukan di dalam field). */
    private function segmen($p)
    {
        $segs = [['node' => [], 'br' => 0]];
        $field = 0;
        foreach (iterator_to_array($p->childNodes) as $c) {
            if ($this->is_w($c, 'pPr')) continue;
            if ($c instanceof DOMElement) {
                foreach ($this->q('.//w:fldChar', $c) as $f) {
                    $j = $f->getAttributeNS(NS_W, 'fldCharType');
                    if ($j === 'begin') $field++;
                    elseif ($j === 'end') $field = max(0, $field - 1);
                }
            }
            if ($field === 0 && $this->run_br($c)) {
                $akhir = count($segs) - 1;
                if (!$segs[$akhir]['node'] && $akhir > 0) { $segs[$akhir - 1]['br_node'][] = $c; $segs[$akhir - 1]['br_lanjut']++; continue; }
                $segs[$akhir]['br_node'] = [$c];
                $segs[$akhir]['br_lanjut'] = 1;
                $segs[] = ['node' => [], 'br' => 0];
                continue;
            }
            $segs[count($segs) - 1]['node'][] = $c;
        }
        foreach ($segs as &$s) $s = array_merge($s, $this->info_segmen($s['node'], $p));
        return $segs;
    }

    private function info_segmen($nodes, $p)
    {
        $teks = ''; $semua_tebal = true; $semua_caps = true; $semua_miring = true; $ada_run = false;
        $gambar = false; $rumus = false; $teks_rumus = '';
        $gp = $this->gaya_par($p);
        foreach ($nodes as $n) {
            if (!$n instanceof DOMElement) continue;
            if ($this->q('descendant-or-self::w:drawing | descendant-or-self::w:pict | descendant-or-self::w:object', $n)) $gambar = true;
            if ($n->namespaceURI === NS_M || $this->q('.//m:oMath', $n)) { $rumus = true; }
            $runs = $this->is_w($n, 'r') ? [$n] : $this->q('.//w:r[not(ancestor::m:oMath)]', $n);
            foreach ($runs as $r) {
                $t = '';
                foreach ($r->childNodes as $c) {
                    if ($this->is_w($c, 't')) $t .= $c->nodeValue;
                    elseif ($this->is_w($c, 'tab')) $t .= ' ';
                    elseif ($this->is_w($c, 'noBreakHyphen')) $t .= '-';
                }
                $teks .= $t;
                if (trim(str_replace("\xC2\xA0", ' ', $t)) === '') continue;
                $ada_run = true;
                if (!$this->run_sifat($r, 'b', $gp)) $semua_tebal = false;
                if (!$this->run_sifat($r, 'i', $gp)) $semua_miring = false;
                if (!$this->run_sifat($r, 'caps', $gp) && $t !== n_upper($t)) $semua_caps = false;
            }
            if ($n->namespaceURI === NS_M || $this->q('.//m:oMath', $n)) {
                foreach ($this->q('.//m:t', $n) as $mt) $teks_rumus .= $mt->nodeValue;
            }
        }
        $teks = n_rapi($teks);
        return ['teks' => $teks, 'tebal' => $ada_run && $semua_tebal, 'miring' => $ada_run && $semua_miring, 'caps' => $ada_run && $semua_caps && n_ada_huruf($teks),
                'gambar' => $gambar, 'rumus' => $rumus, 'kosong' => $teks === '' && !$gambar && !$rumus];
    }

    /** Segmen = baris judul (aturan yang sama dengan segmen_judul di build.py, ditambah judul tebal pendek). */
    private function seg_judul($s, $judul_gaya)
    {
        if ($s['kosong'] || $s['gambar'] || $s['rumus'] || $s['teks'] === '') return false;
        if (n_bagian_baku($s['teks']) !== '' && ($s['tebal'] || $s['caps'] || $judul_gaya)) return true;
        if ($s['tebal'] && n_judul_akhir($s['teks'])) return true;
        // baris tebal pendek tersendiri = (sub)judul; "Note:" dsb. yang diakhiri titik dua bukan judul
        return $s['tebal'] && n_baris_pendek($s['teks'], 15) && !preg_match('/:$/u', $s['teks']);
    }

    // ------------------------------------------------------------------ tahap 2: struktur
    private function paragraf_badan()
    {
        $body = $this->q('/w:document/w:body')[0];
        $hasil = [];
        foreach ($body->childNodes as $c) {
            if ($this->is_w($c, 'p')) $hasil[] = $c;
        }
        return $hasil;
    }

    /** Posisi (indeks paragraf badan) awal isi, judul penutup terakhir, dan judul referensi. */
    private function zona($pars)
    {
        $awal = null; $awal_cadangan = null; $ref = null; $tutup = null;
        foreach ($pars as $i => $p) {
            $jg = $this->par_judul_gaya($p);
            foreach ($this->segmen($p) as $s) {
                if (!(($s['tebal'] || $s['caps'] || $jg) && $s['teks'] !== '')) continue;
                $k = n_bagian_baku($s['teks']);
                if ($k === '') continue;
                if ($awal === null && in_array($k, NORM_AWAL, true)) $awal = $i;
                if ($awal_cadangan === null) $awal_cadangan = $i;
                if (in_array($k, NORM_REFERENSI, true)) $ref = $i;
            }
        }
        if ($awal === null) $awal = $awal_cadangan;
        // lampiran: judul bagian pertama sesudah REFERENCES (akhir zona referensi)
        $lamp = null;
        if ($ref !== null) {
            for ($i = $ref + 1; $i < count($pars) && $lamp === null; $i++) {
                $jg = $this->par_judul_gaya($pars[$i]);
                foreach ($this->segmen($pars[$i]) as $s) {
                    if (($s['tebal'] || $s['caps'] || $jg) && n_bagian_baku($s['teks'], NORM_LAMPIRAN) !== '') { $lamp = $i; break; }
                }
            }
        }
        $this->lampiran = $lamp;
        if ($awal !== null && $ref !== null) {
            for ($i = $ref - 1; $i >= $awal; $i--) {
                $jg = $this->par_judul_gaya($pars[$i]);
                foreach ($this->segmen($pars[$i]) as $s) {
                    if (($s['tebal'] || $s['caps'] || $jg) && n_bagian_baku($s['teks']) !== '' && n_awalan($s['teks'], NORM_PENUTUP)) {
                        $tutup = $i; break 2;
                    }
                }
            }
        }
        return [$awal, $tutup, $ref];
    }

    /** pPr untuk paragraf pecahan: tanpa penomoran daftar dan tanpa pemisah bagian. */
    private function ppr_salin($p)
    {
        $ppr = $this->anak($p, 'pPr');
        if (!$ppr) return null;
        $baru = $ppr->cloneNode(true);
        foreach (['numPr', 'sectPr'] as $k) { if ($x = $this->anak($baru, $k)) $this->hapus($x); }
        return $baru;
    }

    /**
     * Keputusan di setiap batas br: 'pecah' (paragraf baru), 'spasi' (gabung jadi spasi), 'biar'.
     * $zona: 'isi' | 'ref'
     */
    private function putuskan($a, $b, $jumlah_br, $zona, $jg)
    {
        if ($a['kosong'] || $b['kosong']) return 'pecah';                 // br di awal/akhir paragraf
        // satu entri referensi per paragraf; baris tanpa tahun (DOI/URL, sambungan) tetap di entrinya
        if ($zona === 'ref') {
            // FIX-REF-TAHUN-DOI: tahun di dalam URL/DOI bukan penanda entri baru; tahun
            // penerbitan harus berada di dalam kurung (APA) agar entri tidak terbelah.
            $b_bersih = preg_replace('/https?:\S+|10\.\d{4,9}\/\S+/iu', '', $b['teks']);
            return preg_match('/\(\s*(?:1[5-9]\d\d|20\d\d)[a-z]?(?:,\s*[^)]{1,25})?\s*\)|\(\s*n\.\s?d\.\s*\)/iu', $b_bersih) ? 'pecah' : 'spasi';
        }
        if ($jumlah_br >= 2) return 'pecah';                              // baris kosong = ganti paragraf
        if ($this->seg_judul($a, $jg) || $this->seg_judul($b, $jg)) return 'pecah';
        if (preg_match(NORM_RE_LABEL, $a['teks'])) return 'pecah';        // "Table 2" lalu judul tabel
        if ($a['gambar'] || $b['gambar'] || $a['rumus'] && trim(preg_replace('/\s/u', '', $a['teks'])) === ''
            || $b['rumus'] && trim(preg_replace('/\s/u', '', $b['teks'])) === '') return 'pecah';
        if ($a['rumus'] || $b['rumus']) return 'biar';
        // FIX-PITA-KALIMAT: baris baru sesudah titik dua yang diikuti huruf kecil adalah
        // lanjutan kalimat ("These preconditions are:" + "cohesive customary authority...").
        if (preg_match('/:\s*$/u', $a['teks']) && preg_match('/^\p{Ll}/u', $b['teks'])) return 'spasi';
        // baris baru sesudah akhir kalimat (mis. atribusi kutipan wawancara) dipertahankan seperti di
        // Word; baris yang patah di tengah kalimat (hasil salin dari PDF) digabung dengan spasi
        if (preg_match('/[\.!?:;]["\'”’)\]]*$/u', $a['teks'])) return 'biar';
        if (preg_match('/^[\p{Ll}\d,;]/u', $b['teks']) || preg_match('/[\p{L}\d,]$/u', $a['teks'])
            && preg_match('/^\p{L}/u', $b['teks'])) return 'spasi';
        return 'biar';
    }

    private function pecah_paragraf($p, $zona)
    {
        $this->pisah_run_br($p);
        $segs = $this->segmen($p);
        if (count($segs) < 2) return;
        $jg = $this->par_judul_gaya($p);
        $grup = [[0]];
        for ($i = 0; $i < count($segs) - 1; $i++) {
            $k = $this->putuskan($segs[$i], $segs[$i + 1], $segs[$i]['br_lanjut'], $zona, $jg);
            if ($k === 'pecah') {
                foreach ($segs[$i]['br_node'] as $br) $this->hapus($br);
                $a = $segs[$i]; $b = $segs[$i + 1];
                if (!$a['kosong'] && !$b['kosong']) {
                    if ($zona === 'ref') $this->catat('entri referensi dipisah (Shift+Enter)', $b['teks']);
                    elseif ($this->seg_judul($a, $jg)) $this->catat('judul dipisah dari paragraf (Shift+Enter)', $a['teks']);
                    elseif ($this->seg_judul($b, $jg)) $this->catat('judul dipisah dari paragraf (Shift+Enter)', $b['teks']);
                    else $this->catat('paragraf dipisah (Shift+Enter)', $a['teks'] . ' / ' . $b['teks']);
                }
                $grup[] = [$i + 1];
            } else {
                if ($k === 'spasi') {
                    foreach ($segs[$i]['br_node'] as $br) {
                        foreach ($this->q('w:br', $br) as $x) $this->hapus($x);
                        $t = $this->w('t');
                        $t->setAttributeNS('http://www.w3.org/XML/1998/namespace', 'xml:space', 'preserve');
                        $t->nodeValue = ' ';
                        $br->appendChild($t);
                    }
                    $this->catat('baris patah (Shift+Enter) digabung', $segs[$i]['teks'] . ' / ' . $segs[$i + 1]['teks']);
                }
                $grup[count($grup) - 1][] = $i + 1;
            }
        }
        if (count($grup) < 2) return;
        // grup pertama yang berisi tetap di $p; grup kosong (mis. hanya bookmark) ikut grup berikutnya
        $isi = function ($g) use ($segs) {
            foreach ($g as $i) { if (!$segs[$i]['kosong']) return true; }
            return false;
        };
        $simpul = function ($g) use ($segs) {
            $hasil = [];
            foreach ($g as $k => $i) {
                foreach ($segs[$i]['node'] as $n) $hasil[] = $n;
                if ($k < count($g) - 1) { foreach ($segs[$i]['br_node'] as $n) $hasil[] = $n; }
            }
            return $hasil;
        };
        $induk = $p->parentNode;
        $sisip = $p->nextSibling;
        $tunda = [];
        $pertama = true;
        $terakhir = $p;
        foreach ($grup as $g) {
            if ($pertama) {
                if ($isi($g)) $pertama = false;
                continue;
            }
            if (!$isi($g)) { $tunda = array_merge($tunda, $simpul($g)); continue; }
            $baru = $this->w('p');
            if ($ppr = $this->ppr_salin($p)) $baru->appendChild($ppr);
            foreach (array_merge($tunda, $simpul($g)) as $n) $baru->appendChild($n);
            $tunda = [];
            $induk->insertBefore($baru, $sisip);
            $terakhir = $baru;
        }
        foreach ($tunda as $n) $terakhir->appendChild($n);
        // pemisah bagian (section break) milik potongan terakhir
        $ppr = $this->anak($p, 'pPr');
        if ($terakhir !== $p && $ppr && ($sect = $this->anak($ppr, 'sectPr'))) {
            $tp = $this->anak($terakhir, 'pPr');
            if (!$tp) { $tp = $this->w('pPr'); $terakhir->insertBefore($tp, $terakhir->firstChild); }
            $tp->appendChild($sect);
        }
    }

    /** Buang br di awal/akhir paragraf (tidak berarti apa-apa di tata letak). */
    private function rapikan_tepi($p)
    {
        $this->pisah_run_br($p);
        // FIX-URL-SPASI: spasi rusak di tengah URL/DOI ("https://www. evangelizatio.va")
        // dibuang; hanya spasi tepat setelah titik/garis miring di dalam URL.
        foreach ($this->q('.//w:t[not(ancestor::w:txbxContent)]', $p) as $t) {
            $s = $t->nodeValue; $baru = $s;
            for ($k = 0; $k < 4; $k++) {
                $next = preg_replace('/\b(https?:\/\/[^\s]*[.\/])[\x{00A0}\s]+(?=[A-Za-z0-9])/u', '$1', $baru);
                if ($next === $baru) break;
                $baru = $next;
            }
            if ($baru !== $s) {
                $t->nodeValue = $baru;
                $t->setAttributeNS('http://www.w3.org/XML/1998/namespace', 'xml:space', 'preserve');
            }
        }
        foreach (['firstChild' => 'nextSibling', 'lastChild' => 'previousSibling'] as $mulai => $arah) {
            $n = $p->$mulai;
            while ($n) {
                $lanjut = $n->$arah;
                if ($this->is_w($n, 'pPr') || $this->is_w($n, 'bookmarkStart') || $this->is_w($n, 'bookmarkEnd')) { $n = $lanjut; continue; }
                if ($this->run_br($n)) { $this->hapus($n); $n = $lanjut; continue; }
                break;
            }
        }
    }

    private function jadikan_tebal($p)
    {
        foreach ($this->q('.//w:r[not(ancestor::m:oMath)]', $p) as $r) {
            $rpr = $this->anak($r, 'rPr');
            if (!$rpr) { $rpr = $this->w('rPr'); $r->insertBefore($rpr, $r->firstChild); }
            if ($b = $this->anak($rpr, 'b')) $this->hapus($b);
            $b = $this->w('b');
            // urutan skema: rStyle, rFonts, b, ...
            $setelah = null;
            foreach ($rpr->childNodes as $c) { if ($this->is_w($c, 'rStyle') || $this->is_w($c, 'rFonts')) $setelah = $c; }
            $rpr->insertBefore($b, $setelah ? $setelah->nextSibling : $rpr->firstChild);
        }
    }

    /** Judul tebal di awal paragraf + isi pada baris yang sama -> judul jadi paragraf sendiri. */
    private function pisah_judul_sebaris($p)
    {
        $gp = $this->gaya_par($p);
        $awal = []; $teks = '';
        $anak = [];
        foreach ($p->childNodes as $c) { if (!$this->is_w($c, 'pPr')) $anak[] = $c; }
        $i = 0;
        for (; $i < count($anak); $i++) {
            $c = $anak[$i];
            if ($this->is_w($c, 'bookmarkStart') || $this->is_w($c, 'bookmarkEnd')) { $awal[] = $c; continue; }
            if (!$this->is_w($c, 'r')) break;
            $t = '';
            foreach ($this->q('w:t', $c) as $x) $t .= $x->nodeValue;
            if (trim($t) !== '' && !$this->run_sifat($c, 'b', $gp)) break;
            $awal[] = $c; $teks .= $t;
        }
        if ($i >= count($anak)) return false;
        $sisa = '';
        for ($j = $i; $j < count($anak); $j++) $sisa .= $anak[$j]->textContent;
        $judul = n_rapi($teks);
        $sisa_r = n_rapi($sisa);
        $titik = preg_match('/[:\.\x{2013}\x{2014}]$/u', $judul) || preg_match('/^[:\x{2013}\x{2014}]/u', $sisa_r);
        $judul_bersih = n_rapi(preg_replace('/[\s:\.\x{2013}\x{2014}\-]+$/u', '', $judul));
        if (!$titik || $sisa_r === '' || !n_judul_akhir($judul_bersih) || count(preg_split('/\s+/u', $judul_bersih)) > 12) return false;
        // buang tanda baca pemisah di akhir judul / awal isi
        $runs_judul = array_values(array_filter($awal, function ($x) { return $this->is_w($x, 'r'); }));
        for ($k = count($runs_judul) - 1; $k >= 0; $k--) {
            $ts = $this->q('w:t', $runs_judul[$k]);
            if (!$ts) continue;
            $t = end($ts);
            $t->nodeValue = preg_replace('/[\s:\.\x{2013}\x{2014}\-]+$/u', '', $t->nodeValue);
            if (trim($t->nodeValue) !== '') break;
        }
        foreach (array_slice($anak, $i) as $c) {
            if (!$this->is_w($c, 'r')) { if (trim($c->textContent) === '') continue; break; }
            $ts = $this->q('w:t', $c);
            if (!$ts) continue;
            $t = $ts[0];
            $t->nodeValue = preg_replace('/^[\s:\x{2013}\x{2014}]+/u', '', $t->nodeValue);
            if (trim($t->nodeValue) !== '') break;
        }
        $baru = $this->w('p');
        $ppr = $this->ppr_salin($p);
        if ($ppr) {
            if ($ind = $this->anak($ppr, 'ind')) $this->hapus($ind);
            $baru->appendChild($ppr);
        }
        foreach ($awal as $c) $baru->appendChild($c);
        $p->parentNode->insertBefore($baru, $p);
        $this->catat('judul sebaris dipisah dari isinya', $judul_bersih);
        return true;
    }

    /**
     * Text box mengambang berisi label pendek yang dijangkarkan ke paragraf BERTEKS di dekat gambar
     * (mis. "Yes"/"No" di paragraf keterangan "Figure 1.") adalah tempelan di atas gambar, bukan isi
     * naskah: pandoc membacanya sebagai paragraf yang menggantung di kolom. Dibuang dan dicatat.
     */
    private function buang_label_mengambang()
    {
        $this->xp->registerNamespace('wp', 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing');
        $this->xp->registerNamespace('v', 'urn:schemas-microsoft-com:vml');
        $pars = $this->paragraf_badan();
        foreach ($pars as $i => $p) {
            $runs = [];
            foreach ($this->q('w:r', $p) as $r) {
                $kotak = $this->q('.//w:txbxContent', $r);
                if (!$kotak || $this->q('.//wp:inline', $r) || $this->q('.//*[local-name()="pic"]', $r)) continue;
                if (!$this->q('.//wp:anchor', $r) && !$this->q('.//v:shape[contains(@style,"position:absolute")]', $r)) continue;
                $runs[] = $r;
            }
            if (!$runs) continue;
            // teks paragraf itu sendiri (di luar text box)
            $sendiri = '';
            foreach ($this->q('.//w:t[not(ancestor::w:txbxContent)]', $p) as $t) $sendiri .= $t->nodeValue;
            if (n_rapi($sendiri) === '') continue;                // paragraf kosong: diagram shape
            $dekat_gambar = preg_match(NORM_RE_LABEL, preg_replace('/^((?:Figure|Fig\.?|Gambar|Table|Tabel)\s*\d+).*$/isu', '$1', n_rapi($sendiri)))
                || ($i > 0 && $this->q('.//w:drawing | .//w:pict', $pars[$i - 1]));
            if (!$dekat_gambar) continue;
            foreach ($runs as $r) {
                $isi = '';
                $teks_box = $this->q('.//w:txbxContent', $r);
                foreach ($this->q('.//w:t', $teks_box[0]) as $t) $isi .= $t->nodeValue . ' ';
                $isi = n_rapi($isi);
                if ($isi !== '' && count(preg_split('/\s+/u', $isi)) > 6) continue;
                $this->hapus($r);
                $this->catat('label text box mengambang di dekat gambar dibuang', $isi);
            }
        }
    }

    /**
     * Paragraf panjang (kalimat, > 25 kata) yang diberi gaya Heading oleh penulis (mis. abstrak bergaya
     * Heading 1) bukan judul: gaya Heading dicopot agar dibaca sebagai paragraf biasa.
     */
    private function lepas_gaya_judul_palsu()
    {
        foreach ($this->paragraf_badan() as $p) {
            if (!$this->par_judul_gaya($p)) continue;
            $t = $this->teks_p($p);
            if (count(preg_split('/\s+/u', $t)) <= 25) continue;
            $ppr = $this->anak($p, 'pPr');
            if ($ppr && ($ps = $this->anak($ppr, 'pStyle'))) $this->hapus($ps);
            if ($ppr && ($ol = $this->anak($ppr, 'outlineLvl'))) $this->hapus($ol);
            $this->catat('gaya Heading dicopot dari paragraf panjang (bukan judul)', $t);
        }
    }

    public $rapor = [];             // rapor kesesuaian naskah terhadap profil ideal, per bagian

    private function rapor($bagian, $status, $catatan = '')
    {
        $this->rapor[] = ['bagian' => $bagian, 'status' => $status, 'catatan' => $catatan];
    }

    private function ada_superskrip_nomor($p)
    {
        foreach ($this->q('.//w:r[w:rPr/w:vertAlign[@w:val="superscript"]]', $p) as $r) {
            $t = '';
            foreach ($this->q('w:t', $r) as $x) $t .= $x->nodeValue;
            if (preg_match('/[\d\*†‡]/u', $t)) return true;
        }
        return (bool)preg_match('/\p{L}[\d\*]+(\s*,|\s*$)/u', $this->teks_p($p));
    }

    /** Ganti br (Shift+Enter) di paragraf dengan spasi (judul/penulis harus mengalir). */
    private function br_jadi_spasi($p)
    {
        $n = 0;
        foreach ($this->q('.//w:r/w:br[not(@w:type) or @w:type="textWrapping"] | .//w:r/w:cr', $p) as $br) {
            $t = $this->w('t');
            $t->setAttributeNS('http://www.w3.org/XML/1998/namespace', 'xml:space', 'preserve');
            $t->nodeValue = ' ';
            $br->parentNode->replaceChild($t, $br);
            $n++;
        }
        return $n;
    }

    /** Isi paragraf $sumber disambung ke akhir $tujuan (dengan spasi), $sumber dihapus. */
    private function sambung($tujuan, $sumber, $pemisah = ' ')
    {
        if ($pemisah !== '') {
            $r = $this->w('r');
            $t = $this->w('t');
            $t->setAttributeNS('http://www.w3.org/XML/1998/namespace', 'xml:space', 'preserve');
            $t->nodeValue = $pemisah;
            $r->appendChild($t);
            $tujuan->appendChild($r);
        }
        foreach (iterator_to_array($sumber->childNodes) as $c) {
            if (!$this->is_w($c, 'pPr')) $tujuan->appendChild($c);
        }
        $this->hapus($sumber);
    }

    /** Peran paragraf bagian depan menurut profil ideal. */
    private function peran_depan($p)
    {
        $t = $this->teks_p($p);
        if ($t === '') return 'kosong';
        if (preg_match('/^(abstract|abstrak)\b/iu', $t)) return 'abstrak';
        if (preg_match('/^(key[\s\-]*words?|kata[\s\-]*(kata[\s\-]*)?kunci)\b/iu', $t)) return 'kata_kunci';
        if (preg_match('/\b(received|accepted|published|revised|diterima|disetujui|diterbitkan)\s*:/iu', $t)) return 'riwayat';
        if (preg_match('/^(purpose|objectives?|aims?|background|context|design|methods?|methodology|approach|'
            . '(key\s+)?findings|results?|conclusions?|originality|value|implications?|limitations?|novelty|'
            . 'tujuan|latar\s+belakang|metode|hasil|kesimpulan|temuan)\b[^:]{0,30}:/iu', $t)) return 'abstrak_terstruktur';
        if (preg_match('/@/u', $t) && preg_match('/correspond|e-?mail|korespondensi|penulis/iu', $t)) return 'korespondensi';
        if (preg_match(NORM_RE_INSTITUSI, $t)) return 'afiliasi';
        if (mb_strlen($t) <= 400 && n_daftar_nama($t)) return 'nama';
        return 'teks';
    }

    /**
     * Bagian depan dibandingkan dengan profil ideal dan dibetulkan:
     * judul 1 paragraf, penulis 1 paragraf, 1 afiliasi per paragraf, label "Abstract:"/"Keywords:" menyatu
     * dengan isinya. Hasil per bagian dicatat di rapor.
     */
    private function normalkan_depan()
    {
        $pars = $this->paragraf_badan();
        list($awal) = $this->zona($pars);
        if ($awal === null) {
            $this->rapor('Struktur naskah', 'periksa', 'judul bagian utama (INTRODUCTION, dst.) tidak ditemukan');
            return;
        }
        $depan = [];
        for ($i = 0; $i < $awal; $i++) {
            if ($this->teks_p($pars[$i]) !== '' || $this->q('.//w:drawing', $pars[$i])) $depan[] = $pars[$i];
        }
        if (!$depan) { $this->rapor('Judul', 'periksa', 'bagian depan naskah kosong'); return; }
        $peran = array_map(function ($p) { return $this->peran_depan($p); }, $depan);

        // --- penulis: paragraf "nama" pertama yang bernomor / berisi >= 2 nama / diikuti afiliasi,
        //     dan tidak sesudah baris yang diakhiri titik dua (itu anak judul)
        $a_pen = null;
        for ($k = 1; $k < count($depan) && $k < 7; $k++) {
            if ($peran[$k] !== 'nama' || preg_match('/:$/u', $this->teks_p($depan[$k - 1]))) continue;
            $t = $this->teks_p($depan[$k]);
            $nama = array_filter(array_map('trim', preg_split('/\s*(?:,|;|&|\band\b|\bdan\b)\s*/u',
                preg_replace('/[\d\*]+/u', ' ', $t))), 'strlen');
            $ikut = isset($peran[$k + 1]) && in_array($peran[$k + 1], ['afiliasi', 'korespondensi'], true);
            if ($this->ada_superskrip_nomor($depan[$k]) || count($nama) >= 2 || $ikut) { $a_pen = $k; break; }
        }
        // --- judul
        $akhir_judul = $a_pen === null ? 1 : $a_pen;
        $judul = $depan[0];
        $n_br = $this->br_jadi_spasi($judul);
        for ($k = 1; $k < $akhir_judul; $k++) {
            $n_br += $this->br_jadi_spasi($depan[$k]);
            $this->sambung($judul, $depan[$k]);
        }
        if ($akhir_judul > 1) {
            $this->catat('judul digabung menjadi satu paragraf', $this->teks_p($judul));
            $this->rapor('Judul', 'diperbaiki', "terpecah di $akhir_judul paragraf, digabung menjadi satu");
        } elseif ($n_br) {
            $this->rapor('Judul', 'diperbaiki', 'baris baru (Shift+Enter) di judul diganti spasi');
        } else {
            $this->rapor('Judul', 'sesuai');
        }
        // --- penulis
        if ($a_pen === null) {
            $this->rapor('Penulis', 'periksa', 'baris nama penulis tidak dikenali');
        } else {
            $pen = $depan[$a_pen];
            $n_br = $this->br_jadi_spasi($pen);
            $b = $a_pen + 1;
            while ($b < count($depan) && $peran[$b] === 'nama'
                && ($this->ada_superskrip_nomor($depan[$b]) || preg_match('/(,|;|&|\band|\bdan)\s*$/u', $this->teks_p($pen)))) {
                $this->br_jadi_spasi($depan[$b]);
                $this->sambung($pen, $depan[$b]);
                $b++;
            }
            if ($b - $a_pen > 1) {
                $this->catat('nama penulis digabung menjadi satu paragraf', $this->teks_p($pen));
                $this->rapor('Penulis', 'diperbaiki', 'terpecah di ' . ($b - $a_pen) . ' paragraf, digabung menjadi satu');
            } elseif ($n_br) {
                $this->rapor('Penulis', 'diperbaiki', 'baris baru (Shift+Enter) diganti spasi');
            } else {
                $this->rapor('Penulis', 'sesuai', $this->ada_superskrip_nomor($pen) ? '' : 'tanpa nomor afiliasi (boleh bila satu afiliasi)');
            }
        }
        // --- afiliasi: satu afiliasi per paragraf (pecah di Shift+Enter)
        $pars2 = $this->paragraf_badan();
        $n_af = 0; $pecah = 0;
        $mulai_af = $a_pen === null ? 1 : array_search($pen, $pars2, true) + 1;
        foreach (array_slice($pars2, $mulai_af) as $p) {
            if ($this->peran_depan($p) === 'abstrak') break;
            if ($this->peran_depan($p) !== 'afiliasi') continue;
            $n_af++;
            $this->pisah_run_br($p);
            $segs = $this->segmen($p);
            if (count($segs) < 2) continue;
            for ($i = count($segs) - 1; $i >= 1; $i--) {
                if ($segs[$i]['kosong']) continue;
                $baru = $this->w('p');
                if ($ppr = $this->ppr_salin($p)) $baru->appendChild($ppr);
                foreach ($segs[$i]['node'] as $nd) $baru->appendChild($nd);
                $p->parentNode->insertBefore($baru, $p->nextSibling);
                $pecah++;
            }
            foreach ($this->q('w:r', $p) as $r) { if ($this->run_br($r)) $this->hapus($r); }
        }
        if ($pecah) {
            $this->catat('afiliasi dipisah satu per paragraf', "$pecah afiliasi");
            $this->rapor('Afiliasi', 'diperbaiki', "$pecah afiliasi yang digabung Shift+Enter dipisah");
        } else {
            $this->rapor('Afiliasi', $n_af ? 'sesuai' : 'periksa', $n_af ? "$n_af afiliasi" : 'afiliasi tidak ditemukan');
        }
        // --- korespondensi, riwayat
        $peran2 = [];
        foreach ($this->paragraf_badan() as $i => $p) {
            if ($i >= $awal + $pecah) break;
            $peran2[] = [$this->peran_depan($p), $p];
        }
        $ada = function ($x) use ($peran2) { foreach ($peran2 as $r) { if ($r[0] === $x) return $r[1]; } return null; };
        $this->rapor('Korespondensi', $ada('korespondensi') ? 'sesuai' : 'periksa',
            $ada('korespondensi') ? '' : 'email penulis korespondensi tidak ditemukan');
        $this->rapor('Riwayat artikel', $ada('riwayat') ? 'sesuai' : 'info', $ada('riwayat') ? '' : 'tanggal Received/Accepted/Published tidak ada di naskah');
        // --- label Abstract / Keywords berdiri sendiri -> disatukan dengan isinya
        foreach (['abstrak' => ['Abstrak', 'Abstract'], 'kata_kunci' => ['Kata kunci', 'Keywords']] as $jenis => $nm) {
            $lbl = null; $idx = null;
            foreach ($peran2 as $i => $r) { if ($r[0] === $jenis) { $lbl = $r[1]; $idx = $i; break; } }
            if ($lbl === null) {
                $n_st = count(array_filter($peran2, function ($r) { return $r[0] === 'abstrak_terstruktur'; }));
                if ($jenis === 'abstrak' && $n_st) $this->rapor($nm[1], 'sesuai', "abstrak terstruktur ($n_st paragraf)");
                else $this->rapor($nm[1], 'periksa', strtolower($nm[1]) . ' tidak ditemukan');
                continue;
            }
            $t = $this->teks_p($lbl);
            $hanya_label = (bool)preg_match('/^(abstract|abstrak|key[\s\-]*words?|kata[\s\-]*(kata[\s\-]*)?kunci)\s*[:\.\-–—]?\s*$/iu', $t);
            if ($hanya_label && isset($peran2[$idx + 1]) && in_array($peran2[$idx + 1][0], ['teks', 'nama'], true)) {
                $ts = $this->q('.//w:t', $lbl);
                if ($ts && !preg_match('/:\s*$/u', $t)) {
                    $ak = end($ts);
                    $ak->nodeValue = rtrim($ak->nodeValue, " .-–—") . ':';
                }
                $this->sambung($lbl, $peran2[$idx + 1][1]);
                $this->catat('label ' . $nm[1] . ' disatukan dengan isinya', $this->teks_p($lbl));
                $this->rapor($nm[1], 'diperbaiki', 'label di baris sendiri disatukan dengan isinya');
                // daftar peran bergeser
                array_splice($peran2, $idx + 1, 1);
            } else {
                $this->rapor($nm[1], 'sesuai');
            }
        }
    }

    /** Urutan dan kelengkapan bagian utama menurut profil ideal. */
    private function rapor_bagian()
    {
        $pos = [];
        foreach ($this->paragraf_badan() as $i => $p) {
            $k = n_bagian_baku($this->teks_p($p));
            if ($k === '') continue;
            foreach (NORM_URUT_BAGIAN as $nama => $daftar) {
                if (in_array($k, $daftar, true) && !isset($pos[$nama])) $pos[$nama] = $i;
            }
        }
        $hilang = array_diff(array_keys(NORM_URUT_BAGIAN), array_keys($pos));
        $urut = $pos;
        asort($urut);
        $salah = array_keys($urut) !== array_values(array_filter(array_keys(NORM_URUT_BAGIAN), function ($x) use ($pos) { return isset($pos[$x]); }));
        if (isset($pos['RESULTS']) && !isset($pos['CONCLUSION']) && !in_array('CONCLUSION', $hilang, true)) $salah = false;
        $this->rapor('Bagian utama', $hilang || $salah ? 'periksa' : 'sesuai',
            ($hilang ? 'tidak ditemukan: ' . implode(', ', $hilang) . '. ' : '') . ($salah ? 'urutan tidak lazim: ' . implode(' → ', array_keys($urut)) : ''));
    }

    /** Rapor bagian isi dari daftar perubahan & peringatan. */
    private function rapor_isi()
    {
        $kelompok = [
            'Judul bagian' => '/judul (polos|lampiran|sebaris|dipisah)|penomoran otomatis|All Caps|gaya Heading/u',
            'Sub-bab' => '/sub-judul/u',
            'Paragraf' => '/paragraf dipisah|baris patah|Shift\+Enter\) digabung|kontrol konten|track changes|komentar|tersembunyi|tak terlihat|kolom ganda/u',
            'Gambar & tabel' => '/text box|tabel di dalam|label|keterangan/u',
            'Referensi' => '/referensi/u',
        ];
        foreach ($kelompok as $bagian => $pola) {
            $n = 0;
            foreach ($this->perubahan as $p) { if (preg_match($pola, $p['jenis'])) $n++; }
            $w = [];
            foreach ($this->peringatan as $x) {
                if ($bagian === 'Gambar & tabel' && preg_match('/Figure|Table/u', $x)) $w[] = $x;
                if ($bagian === 'Referensi' && preg_match('/referensi/u', $x)) $w[] = $x;
            }
            $status = $w ? 'periksa' : ($n ? 'diperbaiki' : 'sesuai');
            $this->rapor($bagian, $status, trim(($n ? "$n perbaikan. " : '') . implode(' ', $w)));
        }
    }

    /** Atribut run: hapus properti lama lalu tulis nilai baru; null = aktif tanpa val. */
    private function set_run_prop(DOMElement $rPr, $nama, $nilai = null)
    {
        foreach ($this->q('w:' . $nama, $rPr) as $x) $this->hapus($x);
        $e = $this->w($nama);
        if ($nilai !== null) $e->setAttributeNS(NS_W, 'w:val', (string)$nilai);
        $rPr->appendChild($e);
    }

    private function run_teks($teks, DOMElement $rPr)
    {
        $r = $this->w('r');
        $r->appendChild($rPr->cloneNode(true));
        $t = $this->w('t');
        $t->setAttributeNS('http://www.w3.org/XML/1998/namespace', 'xml:space', 'preserve');
        $t->nodeValue = $teks;
        $r->appendChild($t);
        return $r;
    }

    /** Tulis ulang paragraf keterangan: label tebal/regular, isi regular, bukan miring. */
    private function tulis_keterangan(DOMElement $p, $label, $isi)
    {
        $dasar = null;
        foreach ($this->q('w:r/w:rPr', $p) as $rpr) { $dasar = $rpr->cloneNode(true); break; }
        if (!$dasar) $dasar = $this->w('rPr');
        $rpl = $dasar->cloneNode(true);
        $this->set_run_prop($rpl, 'b');
        $this->set_run_prop($rpl, 'bCs');
        $this->set_run_prop($rpl, 'i', '0');
        $this->set_run_prop($rpl, 'iCs', '0');
        $this->set_run_prop($rpl, 'caps', '0');
        $this->set_run_prop($rpl, 'smallCaps', '0');
        $this->set_run_prop($rpl, 'color', '000000');
        $rpt = $dasar->cloneNode(true);
        $this->set_run_prop($rpt, 'b', '0');
        $this->set_run_prop($rpt, 'bCs', '0');
        $this->set_run_prop($rpt, 'i', '0');
        $this->set_run_prop($rpt, 'iCs', '0');
        $this->set_run_prop($rpt, 'caps', '0');
        $this->set_run_prop($rpt, 'smallCaps', '0');
        $this->set_run_prop($rpt, 'color', '000000');
        // Paragraf: sisakan pPr, ganti seluruh isi teks dengan dua run standar.
        foreach (iterator_to_array($p->childNodes) as $c) {
            if ($this->is_w($c, 'pPr')) continue;
            $p->removeChild($c);
        }
        $p->appendChild($this->run_teks($label . ' ', $rpl));
        if ($isi !== '') $p->appendChild($this->run_teks($isi, $rpt));
        // rPr tingkat paragraf tidak boleh mewarisi miring/tebal/caps.
        $ppr = $this->anak($p, 'pPr');
        if ($ppr) {
            $pr = $this->anak($ppr, 'rPr');
            if (!$pr) { $pr = $this->w('rPr'); $ppr->appendChild($pr); }
            $this->set_run_prop($pr, 'b', '0');
            $this->set_run_prop($pr, 'bCs', '0');
            $this->set_run_prop($pr, 'i', '0');
            $this->set_run_prop($pr, 'iCs', '0');
            $this->set_run_prop($pr, 'caps', '0');
            $this->set_run_prop($pr, 'smallCaps', '0');
            $this->set_run_prop($pr, 'color', '000000');
        }
    }

    /** Keterangan Table/Figure: label tebal + titik, isi regular non-miring Sentence case. */
    private function normalkan_keterangan($pars)
    {
        $n = 0;
        foreach ($pars as $p) {
            if ($this->q('.//w:drawing | .//w:pict | .//w:object | .//m:oMath | .//w:fldChar | .//w:hyperlink', $p)) continue;
            $teks = $this->teks_p($p);
            if ($teks === '' || mb_strlen($teks) > 800) continue;
            if (!preg_match(NORM_RE_KETERANGAN, $teks, $m)) continue;
            $kata = n_lower(trim(preg_replace('/\s*\d+$/u', '', $m[1])));
            $kata = preg_replace('/\.$/u', '', $kata);
            $kata = $kata === 'fig' ? 'figure' : $kata;
            if ($kata === 'tabel') $kata = 'table';
            if ($kata === 'gambar') $kata = 'figure';
            if (!in_array($kata, ['table', 'figure'], true)) continue;
            $label = n_kapital_kata($kata) . ' ' . $m[2] . '.';
            $isi = n_sentence_case($m[3]);
            $this->tulis_keterangan($p, $label, $isi);
            $this->catat('keterangan tabel/gambar dinormalkan', $label . ($isi !== '' ? ' ' . $isi : ''));
            $n++;
        }
        return $n;
    }

    public function normalkan_struktur()
    {
        $this->lepas_gaya_judul_palsu();
        $this->normalkan_depan();
        $this->buang_label_mengambang();
        $this->buka_textbox_isi();
        $this->buang_kolom_ganda();
        $pars = $this->paragraf_badan();
        foreach ($pars as $p) $this->pisah_run_br($p);
        list($awal, , $ref) = $this->zona($pars);
        if ($awal === null) {
            $this->peringatan[] = 'Judul bagian utama (INTRODUCTION/PENDAHULUAN, dll.) tidak ditemukan; '
                . 'struktur paragraf tidak dinormalkan, hanya pembersihan.';
        } else {
            foreach ($pars as $i => $p) {
                if ($i < $awal) continue;
                $di_ref = $ref !== null && $i > $ref && ($this->lampiran === null || $i < $this->lampiran);
                $this->pecah_paragraf($p, $di_ref ? 'ref' : 'isi');
            }
        }
        foreach ($this->paragraf_badan() as $p) $this->rapikan_tepi($p);
        // FIX-KETERANGAN-BAKU: caption tabel/gambar dinormalkan sebelum proses build:
        // label tebal/regular + titik, isi regular non-miring Sentence case.
        $this->normalkan_keterangan($this->paragraf_badan());

        // tahap berikut memakai daftar paragraf hasil pemecahan
        $pars = $this->paragraf_badan();
        list($awal, $tutup, $ref) = $this->zona($pars);
        if ($ref !== null && $this->normalkan_lampiran($pars, $ref)) {
            $pars = $this->paragraf_badan();
            list($awal, $tutup, $ref) = $this->zona($pars);
        }
        if ($awal === null) return;
        if ($ref === null) $this->peringatan[] = 'Judul REFERENCES/DAFTAR PUSTAKA tidak ditemukan.';
        if ($tutup !== null && $ref !== null) {
            // judul sebaris "Funding: ...", lalu judul polos (tidak tebal) di bagian penutup
            for ($i = $tutup + 1; $i < $ref; $i++) $this->pisah_judul_sebaris($pars[$i]);
            $pars = $this->paragraf_badan();
            list($awal, $tutup, $ref) = $this->zona($pars);
        }
        foreach ($pars as $i => $p) {
            if ($i < $awal || ($ref !== null && $i > $ref && ($this->lampiran === null || $i < $this->lampiran))) continue;
            if ($this->q('.//w:drawing | .//w:pict | .//m:oMath', $p)) continue;
            $s = $this->info_segmen(iterator_to_array($p->childNodes), $p);
            $jg = $this->par_judul_gaya($p);
            if ($s['teks'] === '' || !n_baris_pendek($s['teks'], 20)) continue;
            $baku = n_bagian_baku($s['teks']);
            $akhir = $tutup !== null && $ref !== null && $i > $tutup && $i < $ref && n_judul_akhir($s['teks'])
                && n_baris_pendek($s['teks'], 12) && !preg_match('/:$/u', $s['teks']);
            if (($baku !== '' || $akhir) && !$s['tebal'] && !$jg) {
                $this->jadikan_tebal($p);
                $this->catat('judul polos ditebalkan', $s['teks']);
            }
            // judul bagian baku dengan penomoran otomatis Word -> tanpa daftar bernomor
            $ppr = $this->anak($p, 'pPr');
            if ($baku !== '' && $ppr && ($np = $this->anak($ppr, 'numPr'))) {
                $this->hapus($np);
                $this->catat('penomoran otomatis judul bagian dilepas', $s['teks']);
            }
            // huruf kapital lewat format (All Caps) pada judul -> teks kapital sungguhan
            if (($baku !== '' || $akhir || $s['tebal'] || $jg) && n_baris_pendek($s['teks'], 20)) {
                $n = 0;
                foreach ($this->q('.//w:r[w:rPr/w:caps]', $p) as $r) {
                    $caps = $this->anak($this->anak($r, 'rPr'), 'caps');
                    if (!self::nilai_on($caps)) continue;
                    foreach ($this->q('w:t', $r) as $t) $t->nodeValue = n_upper($t->nodeValue);
                    $this->hapus($caps);
                    $n++;
                }
                if ($n) $this->catat('format All Caps judul dijadikan huruf kapital', n_upper($s['teks']));
            }
        }
        $pars = $this->paragraf_badan();
        list($awal, $tutup, $ref) = $this->zona($pars);
        if ($ref !== null && $this->gabung_referensi_terpotong($pars, $ref)) {
            $pars = $this->paragraf_badan();
            list($awal, $tutup, $ref) = $this->zona($pars);
        }
        // FIX-LIST-MANUAL: penomoran manual di awal paragraf isi -> daftar Word (w:numPr)
        $this->normalkan_daftar_manual($pars, $awal, $ref);
        $this->tebalkan_subjudul_polos($pars, $awal, $ref);
        $this->periksa_bagian();
        $this->periksa_isi($this->paragraf_badan(), $ref);
        $this->rapor_bagian();
        $this->rapor_isi();
    }

    /** Teks tampak paragraf (w:t, tanpa kode field dan isi text box). */
    private function teks_p($p)
    {
        $t = '';
        foreach ($this->q('.//w:t[not(ancestor::w:txbxContent)]', $p) as $x) $t .= $x->nodeValue;
        return n_rapi($t);
    }

    /**
     * Entri referensi yang terpotong Enter ("... Hungary:" | "Digital media use ...", atau DOI di baris
     * sendiri): paragraf di bagian referensi TANPA tahun disambung ke entri sebelumnya.
     */
    private function gabung_referensi_terpotong($pars, $ref)
    {
        $akhir = $this->lampiran === null ? count($pars) : $this->lampiran;
        $tahun = '/(?:\(\s*(?:1[5-9]\d\d|20\d\d)[a-z]?\s*[,)]|\b(?:n\.\s?d\.|in press|forthcoming|t\.t\.)(?![A-Za-z0-9]))/iu';
        $lalu = null; $n = 0;
        for ($i = $ref + 1; $i < $akhir; $i++) {
            $p = $pars[$i];
            $t = $this->teks_p($p);
            if ($t === '') continue;
            // FIX-REF-TAHUN-DOI: angka tahun di dalam URL/DOI tidak boleh menandai entri baru.
            $t_tanpa_url = preg_replace('/https?:\S+|10\.\d{4,9}\/\S+/iu', '', $t);
            // FIX-REF-URL-SAJA: paragraf yang hanya berisi URL/DOI adalah potongan entri sebelumnya.
            $hanya_url = (bool)preg_match('/^\s*(?:(?:https?:\/\/|doi:\s*|10\.\d{4,9}\/)[^\s]+)\s*$/iu', $t);
            if ($lalu !== null && !$this->par_judul_gaya($p)
                    && ($hanya_url || !preg_match($tahun, $t_tanpa_url))) {
                $sederhana = function ($x) { return preg_replace('/[^\p{L}\p{N}]+/u', ' ', n_lower($x)); };
                $awal_pot = mb_substr(trim($sederhana(preg_replace('/https?:\S+/u', '', $t))), 0, 40);
                if (mb_strlen($awal_pot) >= 20 && mb_strpos($sederhana($this->teks_p($lalu)), $awal_pot) !== false) {
                    $this->peringatan[] = 'Kemungkinan teks ganda di entri referensi (periksa): "'
                        . mb_substr($this->teks_p($lalu), 0, 50) . '..."';
                }
                $spasi = $this->w('r');
                $tt = $this->w('t');
                $tt->setAttributeNS('http://www.w3.org/XML/1998/namespace', 'xml:space', 'preserve');
                $tt->nodeValue = ' ';
                $spasi->appendChild($tt);
                $lalu->appendChild($spasi);
                foreach (iterator_to_array($p->childNodes) as $c) {
                    if (!$this->is_w($c, 'pPr')) $lalu->appendChild($c);
                }
                $this->hapus($p);
                $this->catat('potongan entri referensi disambung ke entri sebelumnya', $t);
                $n++;
                continue;
            }
            $lalu = $p;
        }
        return $n > 0;
    }

    /** Judul lampiran sesudah REFERENCES ("Appendix A. Interview Guide") -> kapital + tebal. */
    private function normalkan_lampiran($pars, $ref)
    {
        $ubah = false;
        for ($i = $ref + 1; $i < count($pars); $i++) {
            $p = $pars[$i];
            $s = $this->info_segmen(iterator_to_array($p->childNodes), $p);
            $t = $s['teks'];
            if (!preg_match('/^(appendix|appendices|lampiran|attachments?|supplementary material)\b/iu', $t)
                || !n_baris_pendek($t, 15) || ($s['tebal'] && $s['caps'])) continue;
            foreach ($this->q('.//w:t', $p) as $x) $x->nodeValue = n_upper($x->nodeValue);
            if (!$s['tebal']) $this->jadikan_tebal($p);
            $this->catat('judul lampiran dikenali', n_upper($t));
            $ubah = true;
        }
        return $ubah;
    }

    /**
     * Sub-judul yang ditulis tanpa format apa pun (tidak tebal/kapital/Heading): paragraf pendek
     * (2-12 kata, tanpa tanda baca akhir), Title Case atau bernomor ("2.1 Data Collection"), di antara
     * akhir kalimat paragraf sebelumnya dan paragraf panjang sesudahnya -> ditebalkan.
     */
    private function tebalkan_subjudul_polos($pars, $awal, $ref)
    {
        if ($awal === null) return;
        $kecil = ['a', 'an', 'the', 'and', 'or', 'of', 'in', 'on', 'for', 'to', 'with', 'by', 'at', 'from', 'as',
                  'vs', 'via', 'dan', 'atau', 'di', 'ke', 'dari', 'pada', 'untuk', 'dengan', 'dalam', 'yang', 'terhadap'];
        $akhir = $ref === null ? count($pars) : $ref;
        for ($i = $awal + 1; $i < $akhir - 1; $i++) {
            $p = $pars[$i];
            $ppr = $this->anak($p, 'pPr');
            if ($this->par_judul_gaya($p) || ($ppr && $this->anak($ppr, 'numPr'))) continue;
            if ($this->q('.//w:drawing | .//w:pict | .//m:oMath | .//w:txbxContent', $p)) continue;
            $s = $this->info_segmen(iterator_to_array($p->childNodes), $p);
            $t = $s['teks'];
            if ($t === '' || $s['tebal'] || $s['caps'] || $s['miring'] || !n_baris_pendek($t, 12) || mb_strlen($t) > 100) continue;
            if (preg_match('/[:?!"”)]$/u', $t) || preg_match(NORM_RE_LABEL, $t)
                || preg_match('/^((?:Figure|Fig\.?|Gambar|Table|Tabel)\s*\d+)/iu', $t)) continue;
            $kata = preg_split('/\s+/u', preg_replace('/^(\d+(\.\d+)*\.?|[A-Z]\.|[IVX]+\.)\s+/u', '', $t));
            $bernomor = (bool)preg_match('/^(\d+(\.\d+)+\.?|\d+\.|[A-Z]\.)\s+\p{Lu}/u', $t);
            if (count($kata) < 2 && !$bernomor) continue;
            $besar = 0; $hitung = 0;
            foreach ($kata as $w) {
                if (in_array(n_lower($w), $kecil, true)) continue;
                $hitung++;
                if (preg_match('/^\p{Lu}/u', $w)) $besar++;
            }
            if (!$bernomor && ($hitung === 0 || $besar / $hitung < 0.75)) continue;
            // diapit: paragraf sebelumnya selesai sebagai kalimat, sesudahnya paragraf isi yang panjang
            $sbl = $this->teks_p($pars[$i - 1]);
            $ssd = $this->teks_p($pars[$i + 1]);
            if ($sbl === '' && $i >= 2) $sbl = $this->teks_p($pars[$i - 2]);
            if ($sbl !== '' && !preg_match('/[\.!?:;"”)\]]$/u', $sbl)) continue;
            if (mb_strlen($ssd) < 120) continue;
            $this->jadikan_tebal($p);
            $this->catat('sub-judul tanpa format ditebalkan', $t);
        }
    }

    /** Pengaturan kolom ganda dari template penulis (w:cols num > 1) dibuang; tata letak dari jurnal. */
    private function buang_kolom_ganda()
    {
        $n = 0;
        foreach ($this->q('//w:sectPr/w:cols') as $c) {
            if ((int)$c->getAttributeNS(NS_W, 'num') > 1 || $this->q('w:col', $c)) { $this->hapus($c); $n++; }
        }
        if ($n) $this->catat('pengaturan kolom ganda dari template penulis dibuang', "$n bagian");
    }

    /**
     * Text box berisi tabel atau teks panjang (bukan bagian diagram) -> isinya dipindah ke badan
     * naskah tepat sesudah paragraf jangkarnya, agar terbaca sebagai tabel/paragraf biasa.
     */
    private function buka_textbox_isi()
    {
        foreach ($this->paragraf_badan() as $p) {
            $bentuk = $this->q('.//*[local-name()="wsp"] | .//*[local-name()="shape" and namespace-uri()="urn:schemas-microsoft-com:vml"]', $p);
            $grup = $this->q('.//*[local-name()="wgp"] | .//*[local-name()="group"]', $p);
            if ($grup) continue;                                   // diagram/grup: dirender build.py
            $runs = [];
            foreach ($this->q('w:r[.//w:txbxContent]', $p) as $r) $runs[] = $r;
            if (count($runs) !== 1 || count($bentuk) > 2) continue;   // satu text box (Choice + Fallback)
            $r = $runs[0];
            $isi = $this->q('.//w:txbxContent', $r)[0];
            $teks = n_rapi(implode(' ', array_map(function ($t) { return $t->nodeValue; }, $this->q('.//w:t', $isi))));
            $ada_tabel = (bool)$this->q('w:tbl', $isi);
            // text box di paragraf kosong = bagian diagram (dirender build.py), kecuali berisi tabel
            if (!$ada_tabel && ($this->teks_p($p) === '' || count(preg_split('/\s+/u', $teks)) <= 15)) continue;
            $sisip = $p->nextSibling;
            foreach (iterator_to_array($isi->childNodes) as $c) {
                if ($c instanceof DOMElement && ($this->is_w($c, 'p') || $this->is_w($c, 'tbl'))) {
                    $p->parentNode->insertBefore($c, $sisip);
                }
            }
            $this->hapus($r);
            $this->catat($ada_tabel ? 'tabel di dalam text box dipindah ke badan naskah'
                : 'isi text box dipindah ke badan naskah', $teks);
        }
    }

    /** Pemeriksaan isi (peringatan, tidak mengubah naskah): nomor gambar/tabel, rujukan, referensi. */
    private function periksa_isi($pars, $ref)
    {
        $label = ['Figure' => [], 'Table' => []];
        $sebut = ['Figure' => [], 'Table' => []];
        foreach ($pars as $i => $p) {
            $t = $this->teks_p($p);
            if (preg_match('/^(Figure|Fig\.?|Gambar|Table|Tabel)\s*(\d+)\s*([\.:]|$|\s+\p{Lu})/u', $t, $m)) {
                $j = preg_match('/^(Table|Tabel)/iu', $m[1]) ? 'Table' : 'Figure';
                $label[$j][] = (int)$m[2];
            } elseif ($ref === null || $i < $ref) {
                if (preg_match_all('/\b(Figures?|Fig\.|Gambar|Tables?|Tabel)\s+(\d+)/iu', $t, $mm, PREG_SET_ORDER)) {
                    foreach ($mm as $x) $sebut[preg_match('/^(Table|Tabel)/iu', $x[1]) ? 'Table' : 'Figure'][] = (int)$x[2];
                }
            }
        }
        foreach ($label as $j => $no) {
            if (!$no) continue;
            $ganda = array_keys(array_filter(array_count_values($no), function ($c) { return $c > 1; }));
            if ($ganda) $this->peringatan[] = "Nomor $j ganda di naskah: " . implode(', ', $ganda) . '.';
            $urut = array_values(array_unique($no));
            if ($urut !== range(1, count($urut))) $this->peringatan[] = "Nomor $j tidak berurutan 1, 2, 3, ...: " . implode(', ', $no) . '.';
            $hilang = array_values(array_diff(array_unique($sebut[$j]), $no));
            if ($hilang) $this->peringatan[] = "$j " . implode(', ', $hilang) . ' disebut di teks tetapi tidak ada di naskah.';
        }
        if ($ref !== null) {
            $tanpa = [];
            $akhir_ref = $this->lampiran === null ? count($pars) : $this->lampiran;
            for ($i = $ref + 1; $i < $akhir_ref; $i++) {
                $t = $this->teks_p($pars[$i]);
                if (mb_strlen($t) < 20) continue;
                if (!preg_match('/\b(1[89]\d\d|20\d\d)[a-z]?\b|n\.\s?d\.|in press|forthcoming|t\.t\./iu', $t)) $tanpa[] = mb_substr($t, 0, 60);
            }
            if ($tanpa) $this->peringatan[] = count($tanpa) . ' entri referensi tanpa tahun, mis.: "' . $tanpa[0] . '..."';
        }
    }

    private function periksa_bagian()
    {
        $ada = [];
        foreach ($this->paragraf_badan() as $p) {
            $s = $this->info_segmen(iterator_to_array($p->childNodes), $p);
            $k = n_bagian_baku($s['teks']);
            if ($k !== '') $ada[$k] = true;
        }
        $wajib = [
            'INTRODUCTION/PENDAHULUAN' => NORM_AWAL,
            'METHOD/METODE' => ['METHOD', 'METHODS', 'METODE', 'RESEARCH METHOD', 'RESEARCH METHODS', 'METODOLOGI',
                                'METHODOLOGY', 'RESEARCH METHODOLOGY', 'METODE PENELITIAN', 'MATERIALS AND METHODS'],
            'RESULTS/HASIL' => ['RESULT', 'RESULTS', 'HASIL', 'RESULT AND DISCUSSION', 'RESULTS AND DISCUSSION',
                                'RESULTS AND DISCUSSIONS', 'HASIL DAN PEMBAHASAN', 'DISCUSSION', 'FINDINGS',
                                'FINDINGS AND DISCUSSION', 'PEMBAHASAN'],
            'CONCLUSION/KESIMPULAN' => ['CONCLUSION', 'CONCLUSIONS', 'KESIMPULAN', 'SIMPULAN', 'PENUTUP',
                                        'CONCLUSION AND RECOMMENDATION', 'CONCLUSIONS AND RECOMMENDATIONS', 'KESIMPULAN DAN SARAN'],
            'REFERENCES/DAFTAR PUSTAKA' => NORM_REFERENSI,
        ];
        foreach ($wajib as $nama => $daftar) {
            $ok = false;
            foreach ($daftar as $k) { if (isset($ada[$k])) { $ok = true; break; } }
            if (!$ok) $this->peringatan[] = "Bagian $nama tidak ditemukan sebagai judul tersendiri.";
        }
    }
}

// ----------------------------------------------------------------------------------------------
//  API
// ----------------------------------------------------------------------------------------------
function normalisasi_docx($masuk, $keluar)
{
    $lap = ['ok' => false, 'perubahan' => [], 'ringkasan' => [], 'peringatan' => []];
    if (!class_exists('ZipArchive') || !class_exists('DOMDocument')) {
        $lap['galat'] = 'PHP tidak punya ekstensi zip/dom';
        return $lap;
    }
    $zip = new ZipArchive();
    if ($zip->open($masuk) !== true) { $lap['galat'] = 'Tidak dapat membuka ' . $masuk; return $lap; }
    $xml = $zip->getFromName('word/document.xml');
    if ($xml === false) { $zip->close(); $lap['galat'] = 'Bukan berkas Word (.docx) yang sah'; return $lap; }
    $n = new NormalisasiDocx();
    $n->muat_gaya($zip->getFromName('word/styles.xml'));
    $num_xml = $zip->getFromName('word/numbering.xml');
    if ($num_xml !== false) $n->muat_numbering($num_xml);
    $hasil = [];
    $muat = function ($isi) {
        $d = new DOMDocument();
        $d->preserveWhiteSpace = true;
        return @$d->loadXML($isi, LIBXML_NONET | LIBXML_PARSEHUGE) ? $d : null;
    };
    $doc = $muat($xml);
    if (!$doc) { $zip->close(); $lap['galat'] = 'word/document.xml rusak'; return $lap; }
    $n->bersihkan($doc, 'isi');
    $n->normalkan_struktur();
    $hasil['word/document.xml'] = $doc->saveXML();
    $num_baru = $n->simpan_numbering();
    if ($num_baru !== null) $hasil['word/numbering.xml'] = $num_baru;
    foreach (['word/footnotes.xml' => 'catatan kaki', 'word/endnotes.xml' => 'catatan akhir'] as $f => $label) {
        $isi = $zip->getFromName($f);
        if ($isi === false) continue;
        $d = $muat($isi);
        if (!$d) continue;
        $n->bersihkan($d, $label);
        $hasil[$f] = $d->saveXML();
    }
    $zip->close();

    if (realpath($masuk) !== realpath($keluar) || !file_exists($keluar)) {
        if (!@copy($masuk, $keluar)) { $lap['galat'] = 'Tidak dapat menulis ' . $keluar; return $lap; }
    }
    $z2 = new ZipArchive();
    if ($z2->open($keluar) !== true) { $lap['galat'] = 'Tidak dapat menulis ' . $keluar; return $lap; }
    foreach ($hasil as $f => $isi) $z2->addFromString($f, $isi);
    $z2->close();

    $lap['ok'] = true;
    $lap['perubahan'] = $n->perubahan;
    $lap['peringatan'] = $n->peringatan;
    $lap['rapor'] = $n->rapor;
    foreach ($n->perubahan as $p) {
        $lap['ringkasan'][$p['jenis']] = ($lap['ringkasan'][$p['jenis']] ?? 0) + 1;
    }
    return $lap;
}

// Dijalankan langsung dari baris perintah (bukan di-require api.php). debug_backtrace() kosong hanya
// di skrip utama; perbandingan jalur berkas tidak dipakai karena di Windows tulisan jalur bisa berbeda.
if (PHP_SAPI === 'cli' && !debug_backtrace()) {
    $arg = array_slice($argv, 1);
    $laporan = null; $diam = false; $pos = [];
    for ($i = 0; $i < count($arg); $i++) {
        if ($arg[$i] === '--laporan') { $laporan = $arg[++$i] ?? null; continue; }
        if ($arg[$i] === '--diam') { $diam = true; continue; }
        $pos[] = $arg[$i];
    }
    if (!$pos) {
        fwrite(STDERR, "pakai: php normalisasi.php naskah.docx [hasil.docx] [--laporan laporan.json] [--diam]\n");
        exit(2);
    }
    $keluar = $pos[1] ?? preg_replace('/\.docx$/i', '', $pos[0]) . '.normal.docx';
    $lap = normalisasi_docx($pos[0], $keluar);
    if ($laporan) file_put_contents($laporan, json_encode($lap, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    if (!$lap['ok']) { fwrite(STDERR, 'Normalisasi gagal: ' . $lap['galat'] . "\n"); exit(1); }
    if (!$diam) {
        echo 'Normalisasi: ' . ($lap['perubahan'] ? count($lap['perubahan']) . ' perubahan' : 'naskah sudah rapi') . " -> $keluar\n";
        foreach ($lap['perubahan'] as $p) echo '  - ' . $p['jenis'] . ($p['teks'] !== '' ? ': ' . $p['teks'] : '') . "\n";
        foreach ($lap['peringatan'] as $p) echo '  ! ' . $p . "\n";
        echo "Rapor kesesuaian dengan profil naskah ideal:\n";
        $tanda = ['sesuai' => '[OK]       ', 'diperbaiki' => '[DIPERBAIKI]', 'periksa' => '[PERIKSA]  ', 'info' => '[INFO]     '];
        foreach ($lap['rapor'] as $r) {
            echo '  ' . ($tanda[$r['status']] ?? $r['status']) . ' ' . str_pad($r['bagian'], 16) . ($r['catatan'] !== '' ? ' ' . $r['catatan'] : '') . "\n";
        }
    }
    exit(0);
}
