"""Тесты конвертера Markdown -> HTML (sqliter.markdown_to_html)."""
from sqliter import markdown_to_html, merge_adjacent_same_tags


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


def test_quote_bold_inside_italic_outside():
    # разметка внутри цитаты работает, '>' внутри **..** — не цитата
    assert markdown_to_html('> **b** and *i*') == '<blockquote><b>b</b> and <i>i</i></blockquote>'
    assert markdown_to_html('**a > b**') == '<b>a &gt; b</b>'


def test_quote_no_space_and_indented():
    assert markdown_to_html('>hello') == '<blockquote>hello</blockquote>'
    assert markdown_to_html('  > hi') == '<blockquote>hi</blockquote>'


def test_link_tme_normalized():
    assert '<a href="https://t.me/x">t</a>' in markdown_to_html('[t](t.me/x)')


def test_link_mention_normalized():
    assert '<a href="https://t.me/durov">t</a>' in markdown_to_html('[t](@durov)')


def test_link_non_url_left_as_is():
    assert markdown_to_html('[t](notaurl)') == '[t](notaurl)'


def test_adjacent_spoilers_merged():
    # ||a|| ||b|| — один спойлер на всё, как шлёт клиент (1 сущность, не 3)
    out = markdown_to_html('||a|| ||b||')
    assert out == '<tg-spoiler>a b</tg-spoiler>'


def test_adjacent_bold_merged():
    assert markdown_to_html('**a** **b**') == '<b>a b</b>'


def test_merge_keeps_visible_text():
    import re
    strip = lambda s: re.sub(r'<[^>]+>', '', s)
    src = '<spoiler>#a</spoiler><spoiler> </spoiler><spoiler>#b</spoiler>'
    assert strip(merge_adjacent_same_tags(src)) == strip(src)
    assert merge_adjacent_same_tags(src) == '<spoiler>#a #b</spoiler>'


def test_merge_does_not_touch_links_or_quotes():
    src = '<a href="https://x.y">a</a> <a href="https://x.y">b</a>'
    assert merge_adjacent_same_tags(src) == src
    src = '<blockquote>a</blockquote>\n<blockquote>b</blockquote>'
    assert merge_adjacent_same_tags(src) == src


def test_merge_multiline_spoiler_wall_single_entity():
    body = '<spoiler>#t</spoiler>' * 110
    merged = merge_adjacent_same_tags(body.replace('</spoiler><spoiler>', '</spoiler> <spoiler>'))
    from sqliter import _count_entity_opens
    assert _count_entity_opens(merged) == 1
