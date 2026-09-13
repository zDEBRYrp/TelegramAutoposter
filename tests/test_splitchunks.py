"""Умная нарезка HTML: длина, баланс тегов, целостность текста."""
import re

from sqliter import ENTITY_TAGS, split_html_smart

_ALT = '|'.join(sorted(ENTITY_TAGS))
TAG_O = re.compile(r'<(' + _ALT + r')(\s[^<>]*)?>')
TAG_C = re.compile(r'</(' + _ALT + r')>')
RE_TEXT = lambda s: re.sub(r'<[^>]+>', '', s)  # noqa: E731


def check(chunks, limit, max_entities=90):
    for c in chunks:
        assert len(c) <= limit, len(c)
        assert len(TAG_O.findall(c)) <= max_entities
        assert len(TAG_O.findall(c)) == len(TAG_C.findall(c))


def test_short_passthrough():
    assert split_html_smart('abc', 4000) == ['abc']


def test_real_post_keeps_quote_and_spoilers():
    body = ('<b>head</b>\n'
            '<blockquote>a\nb\nc</blockquote>\n'
            '<spoiler>#x</spoiler>' * 40)
    chunks = split_html_smart(body, 400)
    assert len(chunks) > 1
    check(chunks, 400)
    assert RETEXT(chunks) == RETEXT([body])
    joined = ''.join(chunks)
    assert '<blockquote>' in joined and '<spoiler>' in joined
    assert '#x' in RETEXT(chunks)


def RETEXT(chunks):
    return RE_TEXT(''.join(chunks))


def test_long_single_line_splits():
    body = '<spoiler>t</spoiler>' * 300
    chunks = split_html_smart(body, 400)
    assert len(chunks) > 1
    check(chunks, 400)
    assert RETEXT(chunks) == RETEXT([body])


def test_malformed_markup_never_hangs():
    body = 'a b &amp c <b>oops\n' + '<spoiler>x</spoiler>' * 200
    chunks = split_html_smart(body, 300)
    check(chunks, 300)
    assert 'a b' in RETEXT(chunks)


def test_entity_cap_splits_spoiler_wall():
    body = '<spoiler>s</spoiler>' * 200
    chunks = split_html_smart(body, 4000, max_entities=90)
    assert len(chunks) > 1
    check(chunks, 4000)
    assert RETEXT(chunks) == RETEXT([body])


def test_custom_emoji_counts_as_entity():
    body = '<emoji id="1">x</emoji>' * 200
    chunks = split_html_smart(body, 4000, max_entities=90)
    assert len(chunks) > 1
    check(chunks, 4000)
    assert RETEXT(chunks) == RETEXT([body])
    assert ''.join(chunks).count('<emoji') == 200
