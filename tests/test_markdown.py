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
