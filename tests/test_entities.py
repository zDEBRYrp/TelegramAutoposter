"""Форматирование из Telegram-клиента (entities) -> HTML."""
from aiogram.types import MessageEntity, User

from sqliter import HTML_READY_MARK, entities_to_html, markdown_to_html, message_to_html


def E(t, off, ln, **kw):
    return MessageEntity(type=t, offset=off, length=ln, **kw)


def test_bold_italic():
    out = entities_to_html('hi yo', [E('bold', 0, 2), E('italic', 3, 2)])
    assert out == '<b>hi</b> <i>yo</i>'


def test_nested():
    out = entities_to_html('abcd', [E('bold', 0, 4), E('italic', 1, 2)])
    assert out == '<b>a<i>bc</i>d</b>'


def test_spoiler_underline_strike():
    out = entities_to_html('abcdefghi',
                           [E('spoiler', 0, 2), E('underline', 2, 2), E('strikethrough', 4, 5)])
    assert out == '<tg-spoiler>ab</tg-spoiler><u>cd</u><s>efghi</s>'


def test_code_pre():
    out = entities_to_html('ab', [E('code', 0, 1), E('pre', 1, 1)])
    assert out == '<code>a</code><pre>b</pre>'


def test_text_link():
    out = entities_to_html('site', [E('text_link', 0, 4, url='https://x.y/?a=1&b=2')])
    assert out == '<a href="https://x.y/?a=1&amp;b=2">site</a>'


def test_text_mention():
    out = entities_to_html('Bob', [E('text_mention', 0, 3, user=User(id=5, is_bot=False, first_name='B'))])
    assert out == '<a href="tg://user?id=5">Bob</a>'


def test_blockquote():
    out = entities_to_html('q', [E('blockquote', 0, 1)])
    assert out == '<blockquote>q</blockquote>'


def test_plain_text_escaped():
    assert entities_to_html('a<b', []) == 'a&lt;b'


def test_unknown_entity_keeps_text():
    out = entities_to_html('@durov', [E('mention', 0, 6)])
    assert out == '@durov'


def test_emoji_surrogate_offsets():
    # 😀 = 2 UTF-16 единицы: bold на эмодзи (0..2), текст после (2..)
    out = entities_to_html('😀ok', [E('bold', 0, 2)])
    assert out == '<b>😀</b>ok'


def test_message_to_html_passthrough_without_entities():
    assert message_to_html('**b**', None) == '**b**'
    assert message_to_html(None, None) == ''


def test_message_to_html_ready_mark_with_entities():
    stored = message_to_html('hi', [E('bold', 0, 2)])
    assert stored.startswith(HTML_READY_MARK)
    assert markdown_to_html(stored) == '<b>hi</b>'  # без двойной конвертации


def test_markdown_still_works_on_plain():
    assert markdown_to_html('**b**') == '<b>b</b>'
