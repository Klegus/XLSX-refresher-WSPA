"""A plan is never replaced by a version that cannot be trusted, and never shown as
current when it is not: the incidents of autumn 2026 replayed on the anonymised files.

Renamed groups, a damaged parse and an unreachable PUW used to end as a hidden plan or
as silently old data. Now the last good version stays published, the API marks it as
possibly out of date and the reason is stored per plan."""
import os
from unittest import mock
from urllib.parse import quote

import mongomock
import pytest

from helpers import fixture_bytes, load_plans  # noqa: F401  (sets sys.path)

PLANS = load_plans()
KEY = 'pielęgniarstwo_zj_3'


def _config():
    return dict(PLANS[KEY], category='st')


@pytest.fixture()
def env(tmp_path):
    import LessonPlanDownloader as L
    import shared_utils
    from LessonPlan import LessonPlan

    client = mongomock.MongoClient()
    db = client['plans_test']
    db.plans_config.insert_one({'_id': 'plans_json', 'plans': {KEY: _config()}})
    state = {'fetch': lambda *a, **k: fixture_bytes(PLANS[KEY])}
    patches = [
        mock.patch.dict(os.environ, {'MONGO_URI': 'mongodb://test', 'MONGO_DB': 'plans_test',
                                     'PLANS_DIRECTORY': str(tmp_path)}),
        mock.patch('pymongo.MongoClient', lambda *a, **k: client),
        mock.patch('shared_utils.MongoClient', lambda *a, **k: client),
        mock.patch.object(L, 'fetch_bytes', lambda *a, **k: state['fetch'](*a, **k)),
        mock.patch('moodle_scanner.login_puw', lambda *a, **k: True),
    ]
    for p in patches:
        p.start()
    shared_utils._mongo_clients.clear()
    L._shared_session = None
    L._download_cache.clear()

    def check(config=None):
        """One check of the plan, like the cycle does it; returns (result, health)."""
        import plan_health
        L._download_cache.clear()
        lp = LessonPlan('user', 'pass', 'mongodb://test', config or _config(), directory=str(tmp_path))
        result = lp.process_and_save_plan()
        return result, plan_health.record(db, lp.collection_name, lp.plan_config['name'], lp.outcome)

    from flask import Flask
    from routes.plans import init_plan_routes
    from shared_utils import plan_collection_name
    app = Flask(__name__)
    init_plan_routes(app, shared_utils.get_semester_collections, db)
    collection = plan_collection_name(_config())
    group = next(iter(PLANS[KEY]['groups']))

    def api():
        return app.test_client().get(f'/api/plan/{quote(collection)}/{quote(group)}')

    yield {'db': db, 'check': check, 'api': api, 'collection': collection, 'state': state, 'group': group}
    for p in patches:
        p.stop()
    shared_utils._mongo_clients.clear()
    L._shared_session = None
    L._download_cache.clear()


def _pretend_file_changed(env):
    """The stored version comes from an earlier file than the one on PUW now."""
    env['db'][env['collection']].update_many({}, {'$set': {'checksum': 'earlier-version'}})


def test_good_plan_is_published_and_healthy(env):
    result, health = env['check']()
    assert result and health['state'] == 'ok' and health['fails'] == 0
    data = env['api']().get_json()
    assert data['plan_html'] and 'stale' not in data


def test_renamed_groups_keep_the_last_good_version_with_a_warning(env):
    """The university renames a group: the generator cannot find the column any more. The
    old pipeline used to publish a damaged plan here, which the validation then hid."""
    env['check']()
    _pretend_file_changed(env)
    config = _config()
    old_name = env['group']
    config['groups'] = dict(config['groups'], **{old_name: 'Grupa której już nie ma w arkuszu'})

    result, health = env['check'](config)
    assert result is None
    assert health['state'] == 'failed' and old_name in health['reason']
    assert env['db'][env['collection']].count_documents({}) == 1  # nothing damaged was saved

    resp = env['api']()
    assert resp.status_code == 200 and resp.get_json()['plan_html']
    assert resp.get_json()['stale']['kind'] == 'changed'


def test_version_that_differs_from_its_source_is_withheld(env):
    from LessonPlan import LessonPlan
    env['check']()
    _pretend_file_changed(env)
    good = LessonPlan._generate_groups_html

    def damaged(self, file_path):
        groups = good(self, file_path)
        return {name: '<table><tr><th>Godziny</th><th>Poniedziałek</th></tr></table>' for name in groups}

    with mock.patch.object(LessonPlan, '_generate_groups_html', damaged):
        result, health = env['check']()
        assert result is False and health['state'] == 'withheld' and health['issues']
        assert env['db'][env['collection']].count_documents({}) == 1
        data = env['api']().get_json()
        assert 'Poniedziałek</th></tr></table>' not in data['plan_html'] and data['stale']['kind'] == 'changed'

        # an admin accepts this file version: it is published at the next check
        env['db'].plan_health.update_one({'_id': env['collection']}, {'$set': {'accept_checksum': health['checksum']}})
        result, health = env['check']()
        assert result and health['state'] == 'ok'
    assert env['db'][env['collection']].count_documents({}) == 2


def test_unreachable_file_warns_only_after_several_checks(env):
    import plan_health
    from shared_utils import UnsafeDownload
    env['check']()

    def login_page(*a, **k):
        raise UnsafeDownload('Downloaded file is not an XLSX (ZIP) file')

    env['state']['fetch'] = login_page
    for attempt in range(1, plan_health.FAILS_BEFORE_STALE + 1):
        result, health = env['check']()
        assert result is None and health['state'] == 'failed' and health['fails'] == attempt
        assert bool(plan_health.stale(health)) == (attempt >= plan_health.FAILS_BEFORE_STALE)
    assert env['api']().get_json()['stale']['kind'] == 'unreachable'

    # back to normal: the warning goes away
    env['state']['fetch'] = lambda *a, **k: fixture_bytes(PLANS[KEY])
    result, health = env['check']()
    assert result is False and health['state'] == 'ok'
    assert 'stale' not in env['api']().get_json()


def test_out_of_date_plan_is_reported_once_per_streak(env):
    import plan_health
    db, name = env['db'], env['collection']
    failed = {'state': 'failed', 'reason': 'W arkuszu nie ma grup: A', 'changed': True}
    plan_health.record(db, name, 'Plan', failed)
    assert [h['_id'] for h in plan_health.take_alerts(db)] == [name]
    plan_health.record(db, name, 'Plan', failed)
    assert plan_health.take_alerts(db) == []          # same streak
    plan_health.record(db, name, 'Plan', {'state': 'ok'})
    plan_health.record(db, name, 'Plan', failed)
    assert len(plan_health.take_alerts(db)) == 1      # a new streak
    # a single failed download is not worth an alert
    plan_health.record(db, 'other', 'Other', {'state': 'failed', 'unreachable': True})
    assert plan_health.take_alerts(db) == []
    # plans removed from the configuration are not reported
    plan_health.record(db, 'gone', 'Gone', failed)
    assert plan_health.take_alerts(db, current={name}) == []


def test_validation_does_not_block_a_plan_whose_source_could_not_be_fetched(env):
    """A timeout during the validation used to be saved as a blocking issue - the plan stayed
    hidden until its file changed."""
    from plan_reconciler import get_blocks, run_validation
    env['check']()

    def timeout(*a, **k):
        raise TimeoutError('read timed out')

    with mock.patch('shared_utils.fetch_bytes', timeout):
        assert run_validation(env['db']) == []
    assert get_blocks(env['db']) == {}
    assert env['api']().status_code == 200


def test_groups_are_not_paired_by_order_when_their_numbers_differ():
    from moodle_scanner import group_renames, safe_group_change
    old = {'groups': {'grupa 1 wg nazwisk': 'a', 'grupa 2 wg nazwisk': 'b'}}
    reshuffled = {'groups': {'gr.2 A-K': 'a', 'gr.1 L-Z': 'b'}}
    assert group_renames(old, reshuffled) == {}
    assert not safe_group_change({'old': old, 'new': reshuffled, 'diff_fields': ['groups']})
