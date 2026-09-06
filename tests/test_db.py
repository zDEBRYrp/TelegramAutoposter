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
