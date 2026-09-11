"""Тесты БД на изолированном tmp-файле (реальный database.db не трогаем)."""
import pytest

from sqliter import DBConnection

CID = 999888777


@pytest.fixture()
def db(tmp_path):
    d = DBConnection(db_path=str(tmp_path / 'test.db'))
    yield d
    try:
        d.c.close()
        d.conn.close()
    except Exception:
        pass


def test_unknown_chat_spam_off_by_default(db):
    assert db.get_channel_spam_status(CID) == 0


def test_channel_post_text_preserves_photo(db):
    db.set_channel_post(CID, photo='pic.jpg')
    db.set_channel_post(CID, text='hello')
    photo, video, text = db.get_channel_post(CID)
    assert photo == 'pic.jpg'
    assert text == 'hello'


def test_channel_post_video_clears_photo(db):
    db.set_channel_post(CID, photo='pic.jpg')
    db.set_channel_post(CID, text='hello')
    db.set_channel_post(CID, video='vid.mp4')
    photo, video, text = db.get_channel_post(CID)
    assert video == 'vid.mp4'
    assert photo == ''
    assert text == 'hello'


def test_channel_post_photo_clears_video(db):
    db.set_channel_post(CID, video='vid.mp4')
    db.set_channel_post(CID, photo='pic.jpg')
    photo, video, _ = db.get_channel_post(CID)
    assert photo == 'pic.jpg'
    assert video == ''


def test_global_photo_video_mutually_exclusive(db):
    db.change_photo('g.jpg')
    s = db.settings()
    assert s[1] == 'g.jpg' and s[2] == ''
    db.change_video('g.mp4')
    s = db.settings()
    assert s[2] == 'g.mp4' and s[1] == ''


def test_channel_timeout(db):
    db.add_channel(CID)
    db.set_channel_timeout(CID, 7)
    assert db.get_channel_timeout(CID) == 7


def test_channel_timeout_clamps_far_deadline(db):
    import time
    db.add_channel(CID)
    far = time.time() + 300 * 60
    db.set_send_next(CID, far)
    db.set_channel_timeout(CID, 5)  # уменьшили — дедлайн подтянулся
    nxt = db.get_send_next(CID)
    assert nxt < far and abs(nxt - (time.time() + 5 * 60)) < 60


def test_channel_timeout_keeps_zero_and_past(db):
    import time
    db.add_channel(CID)
    db.set_send_next(CID, 0.0)
    db.set_channel_timeout(CID, 5)
    assert db.get_send_next(CID) == 0.0  # слать сразу — не трогаем
    past = time.time() - 100
    db.set_send_next(CID, past)
    db.set_channel_timeout(CID, 600)  # увеличили — прошлое не двигаем
    assert abs(db.get_send_next(CID) - past) < 5


def test_global_timeout_clamps_all(db):
    import time
    db.add_channel(CID)
    db.add_channel(CID + 1)
    far = time.time() + 300 * 60
    db.set_send_next(CID, far)
    db.set_send_next(CID + 1, 0.0)
    db.setTimeOut(5)
    assert db.get_send_next(CID) < far
    assert db.get_send_next(CID + 1) == 0.0


def test_unknown_channel_timeout_is_none(db):
    assert db.get_channel_timeout(111222333) is None


def test_connection_has_lock_timeout(tmp_path):
    d = DBConnection(db_path=str(tmp_path / 't.db'))
    try:
        assert d.conn.execute('PRAGMA busy_timeout').fetchone()[0] >= 30000
    finally:
        d.c.close()
        d.conn.close()


def test_clear_channel_post(db):
    db.set_channel_post(CID, photo='pic.jpg')
    db.set_channel_post(CID, text='hello')
    db.clear_channel_post(CID)
    assert db.get_channel_post(CID) == ('', '', '')
