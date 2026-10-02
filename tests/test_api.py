"""End-to-end tests of the plan pipeline on the anonymised real files: download (PUW
mocked) -> sheet generator -> MongoDB (in memory, mongomock) -> public API.

They guard what students actually get: one list entry per plan published in parts,
the attached sheets, meeting calendars, mixed plans, and that a broken download is
reported as a failure instead of silently keeping old data."""
import os
from unittest import mock

import mongomock
import pytest

from helpers import fixture_bytes, load_plans  # noqa: F401  (sets sys.path)

PLANS = load_plans()
# plans published in parts (meeting sheets, on-site + on-line), a mixed plan, a plain plan
WANTED = [
    'pielęgniarstwo_zj_3', 'pielęgniarstwo_zj_5', 'pielęgniarstwo_zj_6', 'pielęgniarstwo_zj_11',
    'pielęgniarstwo_zj_12', 'pielęgniarstwo_pie_st_ii_zj_on_line',
    'pielęgniarstwo_zajęcia_w_siedzibie', 'pielęgniarstwo_zajęcia_on_line',
    'informatyka_inf_i_rok_puw_z_stac', 'informatyka_inf_i_rok_puw_z_on_line',
    'informatyka_inf_iii_puw_z_stacjonarne', 'informatyka_inf_iii_puw_z_on_line',
]
NURSING_CALENDAR = {
    'course': 'Strefa studenta - kierunek Pielęgniarstwo',
    'title': 'PIE ST - sem. 3 weekendowy - organizacja roku akademickiego 2026-2027',
    'seasons': {'zimowy': {str(n): [f'2026-10-{n + 1:02d}'] for n in range(1, 13)}},
}
BY_URL = {PLANS[k]['download_url']: k for k in WANTED}


def _fake_fetch(session, url, timeout=60, require_xlsx=False):
    return fixture_bytes(PLANS[BY_URL[url]])


def _config(key):
    # the anonymised fixtures carry mixed categories; the listing groups plans by mode
    cfg = dict(PLANS[key])
    cfg['category'] = 'st'
    return cfg


@pytest.fixture(scope='module')
def env(tmp_path_factory):
    import LessonPlanDownloader as L
    from LessonPlan import LessonPlan
    from shared_utils import plan_collection_name

    client = mongomock.MongoClient()
    db = client['plans_test']
    db.plans_config.insert_one({'_id': 'plans_json', 'plans': {k: _config(k) for k in WANTED}})
    db.zjazd_calendars.insert_one(NURSING_CALENDAR)

    patches = [
        mock.patch.dict(os.environ, {'MONGO_URI': 'mongodb://test', 'MONGO_DB': 'plans_test',
                                     'PLANS_DIRECTORY': str(tmp_path_factory.mktemp('plans'))}),
        mock.patch('pymongo.MongoClient', lambda *a, **k: client),
        mock.patch('shared_utils.MongoClient', lambda *a, **k: client),
        mock.patch.object(L, 'fetch_bytes', _fake_fetch),
        mock.patch('moodle_scanner.login_puw', lambda *a, **k: True),
    ]
    for p in patches:
        p.start()
    import shared_utils
    shared_utils._mongo_clients.clear()  # the process-wide client cache must not keep a real client
    L._shared_session = None
    L._download_cache.clear()

    results = {}
    for key in WANTED:
        lp = LessonPlan('user', 'pass', 'mongodb://test', _config(key), directory=str(tmp_path_factory.mktemp(key[:20])))
        results[key] = lp.process_and_save_plan()

    from flask import Flask
    from routes.plans import init_plan_routes
    app = Flask(__name__)
    app.json.sort_keys = False
    init_plan_routes(app, shared_utils.get_semester_collections, db)

    yield {'client': app.test_client(), 'db': db, 'results': results,
           'collection': {k: plan_collection_name(_config(k)) for k in WANTED}}
    for p in patches:
        p.stop()
    shared_utils._mongo_clients.clear()
    L._shared_session = None


def _plans(env, faculty):
    from urllib.parse import quote
    resp = env['client'].get(f'/api/plans/st/{quote(faculty)}')
    assert resp.status_code == 200
    return resp.get_json()['plans']


def _plan(env, key, group):
    from urllib.parse import quote
    return env['client'].get(f"/api/plan/{quote(env['collection'][key])}/{quote(group)}")


def test_every_plan_is_downloaded_and_saved(env):
    for key, checksum in env['results'].items():
        assert checksum, key
        doc = env['db'][env['collection'][key]].find_one()
        assert doc['groups'] and all('<table' in html for html in doc['groups'].values()), key


def test_unchanged_file_is_not_saved_again(env):
    from LessonPlan import LessonPlan
    key = 'pielęgniarstwo_zj_3'
    lp = LessonPlan('user', 'pass', 'mongodb://test', _config(key))
    assert lp.process_and_save_plan() is False
    assert env['db'][env['collection'][key]].count_documents({}) == 1


def test_failed_download_is_reported_not_hidden(env):
    """The login page instead of the file (expired session, PUW down) must end as a failure
    of the check - the cycle counts it as an error - and must not touch the stored plan."""
    import LessonPlanDownloader as L
    from LessonPlan import LessonPlan
    from shared_utils import UnsafeDownload

    def login_page(*a, **k):
        raise UnsafeDownload('Downloaded file is not an XLSX (ZIP) file')

    key = 'pielęgniarstwo_zj_5'
    before = env['db'][env['collection'][key]].count_documents({})
    L._download_cache.clear()
    with mock.patch.object(L, 'fetch_bytes', login_page):
        assert LessonPlan('user', 'pass', 'mongodb://test', _config(key)).process_and_save_plan() is None
    assert env['db'][env['collection'][key]].count_documents({}) == before


def test_one_mongo_client_per_process():
    """A client per call or per plan exhausted mongod's file descriptors in production."""
    import shared_utils
    shared_utils._mongo_clients.clear()
    with mock.patch('shared_utils.MongoClient', side_effect=lambda uri: object()) as factory:
        assert shared_utils.mongo_client('mongodb://a') is shared_utils.mongo_client('mongodb://a')
        assert factory.call_count == 1
    shared_utils._mongo_clients.clear()


def test_check_cycle_counts_failed_plan_as_error():
    import main
    manager = main.LessonPlanManager.__new__(main.LessonPlanManager)
    manager.plan_name = 'x'
    manager.lesson_plan = mock.Mock(plan_config={}, process_and_save_plan=mock.Mock(return_value=None))
    manager.status_checker = mock.Mock()
    with mock.patch.object(main.db.plans_config, 'find_one', return_value=None), \
            pytest.raises(main.PlanCheckFailed):
        import asyncio
        asyncio.run(manager.check_once())


def test_meeting_sheets_are_one_list_entry(env):
    nursing = _plans(env, 'Pielęgniarstwo')
    sem3 = [p for p in nursing if p['semester'] == 3]
    assert len(sem3) == 1
    assert sem3[0]['id'] == env['collection']['pielęgniarstwo_zj_3'] and sem3[0]['companion']
    assert 'zjazd' not in sem3[0]['display_name']
    # weekend studies: the on-line sheet is attached, not listed
    names = [p['name'] for p in nursing]
    assert not any(n.endswith('zajęcia on-line') for n in names)


def test_groups_are_in_natural_order(env):
    import re
    for plan in _plans(env, 'Pielęgniarstwo'):
        numbers = [int(m.group(1)) for g in plan['groups'] if (m := re.match(r'Grupa (\d+)', g))]
        assert numbers == sorted(numbers), plan['name']


def test_plan_of_a_group_brings_the_other_meetings(env):
    group = next(iter(PLANS['pielęgniarstwo_zj_3']['groups']))
    data = _plan(env, 'pielęgniarstwo_zj_3', group).get_json()
    assert data['meeting'] == '3'
    assert [(p['label'], p['meeting']) for p in data['parts']] == [
        ('zjazd 5', '5'), ('zjazd 6', '6'), ('zjazd 11', '11'), ('zjazd 12', '12'), ('zajęcia on-line', None)]
    for part in data['parts']:
        if part['meeting']:
            assert list(part['groups']) == [group]  # only the student's own group
        assert part['zjazdy'] == NURSING_CALENDAR['seasons']['zimowy']
    assert data['zjazdy'] == NURSING_CALENDAR['seasons']['zimowy']


def test_cells_reach_the_frontend_as_separate_classes(env):
    group = next(iter(PLANS['pielęgniarstwo_zj_3']['groups']))
    data = _plan(env, 'pielęgniarstwo_zj_3', group).get_json()
    for html in [data['plan_html']] + [h for p in data['parts'] for h in p['groups'].values()]:
        assert '\n' not in html
        # a class never starts with its meeting numbers or lecturer (lost lecturer line)
        for lesson in __import__('re').findall(r'<div data-lesson>(.*?)</div>', html):
            assert not __import__('re').match(r'\s*(zj\b|zj\.|mgr\b|dr\b|sala\b)', lesson), lesson


def test_weekend_and_puw_plans_get_their_on_line_sheet(env):
    for onsite in ('pielęgniarstwo_zajęcia_w_siedzibie', 'informatyka_inf_iii_puw_z_stacjonarne'):
        group = next(iter(PLANS[onsite]['groups'] or {'cały kierunek': 0}))
        data = _plan(env, onsite, group).get_json()
        assert [p['label'] for p in data['parts']] == ['zajęcia on-line'], onsite
        assert data['companion']['label'] == 'zajęcia on-line'  # older frontends


def test_mixed_plan_gets_its_on_line_sheet(env):
    from urllib.parse import quote
    groups = list(PLANS['informatyka_inf_i_rok_puw_z_stac']['groups'])[:2]
    resp = env['client'].post(f"/api/plan/{quote(env['collection']['informatyka_inf_i_rok_puw_z_stac'])}/mixed",
                              json={'groups': groups})
    data = resp.get_json()
    assert resp.status_code == 200 and set(data['group_htmls']) == set(groups)
    assert [p['label'] for p in data['parts']] == ['zajęcia on-line']


def test_renamed_group_keeps_working_under_its_old_name(env):
    """The university renames groups mid-semester; saved selections and calendar links use
    the old name."""
    key = 'pielęgniarstwo_zj_3'
    group = next(iter(PLANS[key]['groups']))
    plans = env['db'].plans_config.find_one({'_id': 'plans_json'})['plans']
    plans[key]['group_renames'] = {'Grupa dawna nazwa': group}
    env['db'].plans_config.update_one({'_id': 'plans_json'}, {'$set': {'plans': plans}})
    try:
        data = _plan(env, key, 'Grupa dawna nazwa').get_json()
        assert data['group_name'] == group and data['plan_html']
    finally:
        del plans[key]['group_renames']
        env['db'].plans_config.update_one({'_id': 'plans_json'}, {'$set': {'plans': plans}})


def test_unknown_plan_or_group_is_404(env):
    assert env['client'].get('/api/plan/plans_nope/x').status_code == 404
    assert _plan(env, 'pielęgniarstwo_zj_3', 'Grupa 99 nie istnieje').status_code == 404
