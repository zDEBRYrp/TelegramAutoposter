"""Тесты конвертера Markdown -> HTML (sqliter.markdown_to_html)."""
from sqliter import markdown_to_html


def test_bold():
    assert '<b>b</b>' in markdown_to_html('**b**')
    assert '<b>b</b>' in markdown_to_html('__b__')


def test_italic():
    assert '<i>i</i>' in markdown_to_html('*i*')


def test_strike():
    assert '<s>s</s>' in markdown_to_html('~~s~~')


def test_inline_code():
    assert '<code>c</code>' in markdown_to_html('`c`')


def test_code_block():
    assert '<pre>' in markdown_to_html('```py\ncode\n```')
    assert '<pre>code</pre>' in markdown_to_html('```code```')


def test_code_not_formatted_inside():
    # ** внутри кода не должно становиться жирным
    assert markdown_to_html('`**notbold**`') == '<code>**notbold**</code>'


def test_link():
    assert '<a href="https://x.y">t</a>' in markdown_to_html('[t](https://x.y)')


def test_html_escaped():
    out = markdown_to_html('<b>raw</b>')
    assert '&lt;b&gt;' in out


def test_empty():
    assert markdown_to_html('') == ''
    assert markdown_to_html(None) is None


def test_spoiler():
    assert '<tg-spoiler>s</tg-spoiler>' in markdown_to_html('||s||')


def test_spoiler_with_bold_inside():
    out = markdown_to_html('||**b**||')
    assert '<tg-spoiler><b>b</b></tg-spoiler>' in out


def test_single_pipe_untouched():
    assert markdown_to_html('a | b') == 'a | b'


def test_quote():
    out = markdown_to_html('> hello')
    assert out == '<blockquote>hello</blockquote>'


def test_quote_multiline_grouped():
    out = markdown_to_html('> a\n> b\nplain')
    assert out == '<blockquote>a\nb</blockquote>\nplain'


def test_quote_with_markdown_inside():
    out = markdown_to_html('> **b**')
    assert out == '<blockquote><b>b</b></blockquote>'


def test_link_tme_normalized():
    assert '<a href="https://t.me/x">t</a>' in markdown_to_html('[t](t.me/x)')


def test_link_mention_normalized():
    assert '<a href="https://t.me/durov">t</a>' in markdown_to_html('[t](@durov)')


def test_link_non_url_left_as_is():
    assert markdown_to_html('[t](notaurl)') == '[t](notaurl)'
