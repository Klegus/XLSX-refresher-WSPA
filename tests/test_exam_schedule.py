"""Tests for the exam timetable parser and plan matching.

Cases come from the real 2025/2026 file typed by hand at the university.
Run: pytest tests/test_exam_schedule.py  (or: python tests/test_exam_schedule.py)
"""
import os
import sys
from datetime import date, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import exam_schedule as es  # noqa: E402

FACULTIES = [
    'Administracja', 'Architektura', 'Finanse i rachunkowość', 'Gospodarka przestrzenna', 'Informatyka',
    'Media i dziennikarstwo', 'Pedagogika przedszkolna i wczesnoszkolna', 'Pielęgniarstwo', 'Praca socjalna',
    'Projektowanie wnętrz', 'Stosunki międzynarodowe', 'Transport', 'Zarządzanie',
]


def programmes(text):
    result, unknown = es.parse_programmes(text, FACULTIES)
    return result, unknown


def test_academic_year_detection():
    assert es.academic_year_from_text('Rok akademicki 2026/2027') == '2026/2027'
    assert es.academic_year_from_text('Terminarz zaliczeń 2026-2027') == '2026/2027'
    assert es.academic_year_from_text('rok akad. 26/27') == '2026/2027'
    assert es.academic_year_from_text('Terminarz 2026/27') == '2026/2027'
    assert es.academic_year_from_text('Szkolenie 2023/2024') == '2023/2024'
    assert es.academic_year_from_text('01.10.2026') is None  # a date is not a year range
    assert es.academic_year_from_text('2026/2028') is None


def test_current_academic_year_switches_in_september():
    assert es.current_academic_year(date(2026, 8, 31)) == '2025/2026'
    assert es.current_academic_year(date(2026, 9, 1)) == '2026/2027'
    assert es.current_academic_year(date(2027, 1, 15)) == '2026/2027'


def test_programme_typos_and_lists():
    assert programmes('Informatyla')[0] == [{'faculty': 'Informatyka', 'degree': None}]
    assert programmes('Administarcja II stopnia')[0] == [{'faculty': 'Administracja', 'degree': 'II stopnia'}]
    assert programmes('Administracja - studia II stopnia')[0] == [{'faculty': 'Administracja', 'degree': 'II stopnia'}]
    assert programmes('Pedagogika')[0] == [{'faculty': 'Pedagogika przedszkolna i wczesnoszkolna', 'degree': None}]
    assert programmes('Projektowanie wnetrz')[0] == [{'faculty': 'Projektowanie wnętrz', 'degree': None}]
    faculties = [p['faculty'] for p in programmes('Media i Dziennikarstwo/Zatrządzanie')[0]]
    assert faculties == ['Media i dziennikarstwo', 'Zarządzanie']
    assert programmes('wszystkie kierunki')[0] == es.ALL
    assert programmes('wszytskie kierunki')[0] == es.ALL
    assert programmes('Kosmonautyka')[1] == ['Kosmonautyka']  # unknown is reported, not guessed


def test_modes():
    puw = 'niestacjonarny z wykorzystaniem metod i technik kształcenia na odległość PUW'
    assert es.parse_modes('stacjonarny') == ['st']
    assert es.parse_modes('stacjonarmy') == ['st']
    assert es.parse_modes('niestacjonarny') == ['nst']
    assert es.parse_modes(puw) == ['nst_puw']
    assert es.parse_modes('stacjonarny, niestacjonarny, ' + puw) == ['nst', 'nst_puw', 'st']
    assert es.parse_modes('stacjonarny/' + puw) == ['nst_puw', 'st']
    assert es.parse_modes('wszystkie tryby') == es.ALL
    assert es.parse_modes(None) == es.ALL


def test_years():
    assert es.parse_years('I') == [1]
    assert es.parse_years('II i III') == [2, 3]
    assert es.parse_years('sem.2') == [1]
    assert es.parse_years('sem. 5') == [3]
    assert es.parse_years(None) == es.ALL


def test_times_and_forms():
    assert es.parse_time(time(8, 15)) == ('08:15', '08:15')
    assert es.parse_time('09:00 - 21:00') == ('09:00', '09:00 - 21:00')
    assert es.parse_time('cały dzień') == (None, 'cały dzień')
    assert es.parse_date('16.01.2026') == date(2026, 1, 16)
    form = es.parse_form('Egzamin poprawkowy/ Zaliczenie poprawkowe')
    assert form['kinds'] == ['exam', 'credit'] and form['retake']


def entry(**kw):
    base = {'programmes': [{'faculty': 'Informatyka', 'degree': None}], 'years': [2], 'modes': ['st'], 'group': None}
    base.update(kw)
    return base


def test_matching():
    # Right programme, year and mode
    assert es.entry_matches(entry(), 'Informatyka', 'I stopnia', 2, 'st')
    # Wrong year / mode / degree
    assert not es.entry_matches(entry(), 'Informatyka', 'I stopnia', 3, 'st')
    assert not es.entry_matches(entry(), 'Informatyka', 'I stopnia', 2, 'nst_puw')
    assert not es.entry_matches(entry(), 'Informatyka', 'II stopnia', 2, 'st')
    # Unspecified degree means first cycle, explicit II stopnia only matches master's
    master = entry(programmes=[{'faculty': 'Informatyka', 'degree': 'II stopnia'}], years=[1], modes=['nst_puw'])
    assert es.entry_matches(master, 'Informatyka', 'II stopnia', 1, 'nst_puw')
    assert not es.entry_matches(master, 'Informatyka', 'I stopnia', 1, 'nst_puw')
    # "wszystkie kierunki" / all modes / all years
    assert es.entry_matches(entry(programmes=es.ALL, years=es.ALL, modes=es.ALL), 'Transport', 'I stopnia', 4, 'nst')
    # Specialisation scope
    spec = entry(group='Sp.: Sztuczna inteligencja')
    assert es.entry_matches(spec, 'Informatyka', 'I stopnia', 2, 'st', ['Sp.: Sztuczna inteligencja gr.1'])
    assert not es.entry_matches(spec, 'Informatyka', 'I stopnia', 2, 'st', ['Sp.: Grafika komputerowa'])


def test_real_timetable_fixture_parses_completely():
    """The anonymised 2025/2026 timetable: every row recognised, typos corrected."""
    path = os.path.join(os.path.dirname(__file__), 'fixtures', 'timetable.xlsx')
    with open(path, 'rb') as f:
        entries, problems = es.parse_workbook(f.read(), FACULTIES)
    assert problems == []
    assert len(entries) == 213
    corrections = {c for e in entries for c in e['corrections']}
    assert 'kierunek „Informatyla” → Informatyka' in corrections
    assert 'tryb „stacjonarmy” → stacjonarny' in corrections
    # every row is tied to a programme (or explicitly to all of them)
    assert all(e['programmes'] == es.ALL or e['programmes'] for e in entries)


if __name__ == '__main__':
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e!r}")
    sys.exit(1 if failed else 0)
