"""
Human-readable names for lesson plans.

Plan names are built by the Moodle scanner from the Excel file name and the
sheet name, e.g.

    "Informatyka - studia I stopnia - st IV - semestr zimowy - INF st IV"
    "Administracja - studia II stopnia - nst puw I - semestr zimowy - ADM MUZ I PUW - z on-line"
    "Pielęgniarstwo - studia I stopnia - st II - semestr 3 - zimowy - zj11"

The file part follows the university's naming convention and is parsed here;
the sheet part is typed by hand and is only mined for the variant (on-line /
on-site meetings, practical classes, a single meeting, ...). Anything that does
not match falls back to a cleaned-up version of the original name.
"""
import re

ROMAN = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10}

DEGREES = [
    (re.compile(r'jednolite\s+magisterskie', re.I), 'jednolite magisterskie'),
    (re.compile(r'II\s+stopnia', re.I), 'II stopnia'),
    (re.compile(r'I\s+stopnia', re.I), 'I stopnia'),
]

MODES = {
    'st': 'stacjonarne',
    'nst': 'niestacjonarne',
    'nst puw': 'niestacjonarne z e-learningiem (PUW)',
}
MODES_SHORT = {'st': 'stacjonarne', 'nst': 'niestacjonarne', 'nst puw': 'PUW'}

# Mode + year of study right after the degree: "st IV", "nst puw II", "nst III"
MODE_YEAR_RE = re.compile(r'^\s*(nst\s*puw|nst|st)\s+([IVX]+)\b', re.I)
SEMESTER_NUM_RE = re.compile(r'semestr\s+(\d{1,2})', re.I)
SEASON_RE = re.compile(r'\b(zimowy|letni)\b', re.I)

# Variant markers found in sheet names / file suffixes (order matters)
VARIANTS = [
    (re.compile(r'zaj[eę]cia\s+praktyczne', re.I), 'zajęcia praktyczne'),
    (re.compile(r'zaj[eę]cia\s+w\s+siedzibie', re.I), 'zajęcia w siedzibie'),
    (re.compile(r'zaj[eę]cia\s+on-?line', re.I), 'zajęcia on-line'),
    (re.compile(r'\bzj\.?\s*(\d{1,2})\b', re.I), 'zjazd {0}'),
    (re.compile(r'\bz\.?\s*on-?line\b|\bzj\.?\s+on-?line\b', re.I), 'zjazdy on-line'),
    (re.compile(r'\bz\.?\s*stac\w*', re.I), 'zjazdy w siedzibie'),
]
SCHEDULE_KIND = [
    (re.compile(r'\(dzienne\)', re.I), 'tryb dzienny'),
    (re.compile(r'\(weekendowe\)', re.I), 'tryb weekendowy'),
]


def _split(name):
    return [p.strip() for p in re.split(r'\s+-\s+', name or '') if p.strip()]


def _clean_fallback(name):
    text = re.sub(r'\s+', ' ', str(name or '')).strip()
    return text or 'Plan zajęć'


def describe_plan(name):
    """Parse a plan name into readable parts.

    Returns a dict with: faculty, degree, mode, mode_label, year, semester,
    season, variant, short_name, display_name, parsed (bool).
    """
    info = {'faculty': None, 'degree': None, 'mode': None, 'mode_label': None, 'year': None,
            'semester': None, 'season': None, 'variant': None, 'parsed': False}
    parts = _split(name)
    if len(parts) < 3:
        info['short_name'] = info['display_name'] = _clean_fallback(name)
        return info

    info['faculty'] = parts[0]
    rest = ' - '.join(parts[1:])

    for pattern, label in DEGREES:
        if pattern.search(parts[1]):
            info['degree'] = label
            break

    # Mode + year sit in the part after "studia ..."
    after_degree = ' - '.join(parts[2:])
    m = MODE_YEAR_RE.match(after_degree)
    if m:
        mode = re.sub(r'\s+', ' ', m.group(1).lower())
        info['mode'] = mode
        info['mode_label'] = MODES.get(mode)
        info['year'] = ROMAN.get(m.group(2).upper())

    season = SEASON_RE.search(rest)
    info['season'] = season.group(1).lower() if season else None
    sem = SEMESTER_NUM_RE.search(rest)
    if sem:
        info['semester'] = int(sem.group(1))
    elif info['year'] and info['season']:
        info['semester'] = info['year'] * 2 - (1 if info['season'] == 'zimowy' else 0)

    variants = []
    for pattern, label in SCHEDULE_KIND + VARIANTS:
        found = pattern.search(rest)
        if found and label.split(' ')[0] not in ' '.join(variants):
            variants.append(label.format(*found.groups()) if found.groups() else label)
    info['variant'] = ', '.join(variants) or None

    info['parsed'] = bool(info['degree'] and info['mode'] and info['year'])
    if not info['parsed']:
        info['short_name'] = info['display_name'] = _clean_fallback(name)
        return info

    when = f"rok {info['year']}"
    if info['semester']:
        when += f", semestr {info['semester']}"
    short = [when, info['degree'], MODES_SHORT.get(info['mode'], info['mode'])]
    if info['variant']:
        short.append(info['variant'])
    info['short_name'] = ' · '.join(short)

    display = f"{info['faculty']}, {info['degree']}, {info['mode_label']} – {when}"
    if info['variant']:
        display += f" ({info['variant']})"
    info['display_name'] = display
    return info
