"""Every real plan (anonymised fixtures) must parse the same way as recorded in
golden.json, and must pass source validation. A failure here means a change in
the parser altered what students see - check it, then refresh with
tests/update_golden.py if the change was intended."""
import pytest

from helpers import fixture_bytes, fixture_path, load_golden, load_plans, summarize

PLANS = load_plans()
GOLDEN = load_golden()


@pytest.mark.parametrize('plan_id', sorted(PLANS))
def test_plan_matches_golden(plan_id):
    assert summarize(PLANS[plan_id]) == GOLDEN[plan_id]


@pytest.mark.parametrize('plan_id', sorted(PLANS))
def test_plan_passes_source_validation(plan_id):
    assert GOLDEN[plan_id]['validation'] in ('ok', 'info'), GOLDEN[plan_id]


@pytest.mark.parametrize('plan_id', sorted(PLANS))
def test_scanner_reproduces_config(plan_id):
    """The Moodle scanner reading the same file yields the same plan configuration."""
    import os
    from moodle_scanner import process_excel_file

    cfg = PLANS[plan_id]
    filename = os.path.basename(fixture_path(cfg))
    scanned = {p['sheet_name']: p for p in process_excel_file(fixture_path(cfg), cfg['download_url'], filename)}
    got = scanned[cfg['sheet_name']]
    for field in ('name', 'faculty', 'groups', 'group_aliases', 'category', 'mixed'):
        assert got.get(field) == cfg.get(field), field


def test_fixtures_contain_no_real_lecturer_names():
    import re
    import openpyxl
    title = re.compile(r'(?:prof|dr|mgr|inż|lek|hab|arch)\.?\s+(?!Jan Nazwisko)[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]{2,}\s+[A-ZĄĆĘŁŃÓŚŹŻ]')
    for cfg in PLANS.values():
        for ws in openpyxl.load_workbook(fixture_path(cfg), read_only=True).worksheets:
            for row in ws.iter_rows(values_only=True):
                for v in row:
                    assert not (isinstance(v, str) and title.search(v)), v
