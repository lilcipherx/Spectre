"""Tests for the pure text-layout logic in spectre.renderer."""

from spectre.renderer import RenderLine, TextBuffer


def _kinds(lines: list[RenderLine]) -> list[str]:
    return [line.kind for line in lines]


def _texts(lines: list[RenderLine]) -> list[str]:
    return [line.text for line in lines]


def test_empty_buffer_renders_nothing():
    frame = TextBuffer().render(width_body_chars=10, height_lines=5)
    assert frame.lines == []
    assert frame.rx_active is False
    assert frame.last_final_ts is None


def test_single_final_has_timestamp_on_first_line_only():
    buf = TextBuffer()
    buf.push_final("one two three four five six", ts=1_700_000_000.0)
    frame = buf.render(width_body_chars=10, height_lines=5)
    lines = frame.lines
    assert len(lines) >= 1
    assert lines[0].timestamp is not None
    for line in lines[1:]:
        assert line.timestamp is None
    assert all(line.kind == "final" for line in lines)
    assert all(len(line.text) <= 10 for line in lines)


def test_separator_between_messages_but_not_leading():
    buf = TextBuffer()
    buf.push_final("first", ts=1_700_000_000.0)
    buf.push_final("second", ts=1_700_000_001.0)
    frame = buf.render(width_body_chars=20, height_lines=10)
    kinds = _kinds(frame.lines)
    # Expected pattern: final, separator, final
    assert kinds == ["final", "separator", "final"]
    assert frame.lines[0].timestamp is not None
    assert frame.lines[2].timestamp is not None


def test_partial_is_last_and_flagged():
    buf = TextBuffer()
    buf.push_final("hello", ts=1_700_000_000.0)
    buf.push_partial("live text here")
    frame = buf.render(width_body_chars=40, height_lines=10)
    assert frame.rx_active is True
    assert frame.lines[-1].kind == "partial"
    # Partial should be prefixed with the " ›" marker glyph.
    assert "\u203a" in frame.lines[-1].text
    assert "live text here" in frame.lines[-1].text


def test_partial_replaced_not_appended():
    buf = TextBuffer()
    buf.push_partial("first guess")
    buf.push_partial("second guess")
    frame = buf.render(width_body_chars=40, height_lines=5)
    partials = [line for line in frame.lines if line.kind == "partial"]
    assert len(partials) == 1
    assert "second guess" in partials[0].text


def test_final_promotes_and_clears_partial():
    buf = TextBuffer()
    buf.push_partial("maybe")
    buf.push_final("confirmed", ts=1_700_000_000.0)
    frame = buf.render(width_body_chars=40, height_lines=5)
    assert not any(line.kind == "partial" for line in frame.lines)
    assert any(line.text == "confirmed" for line in frame.lines)
    assert frame.rx_active is False


def test_truncates_to_height_preserving_freshest():
    buf = TextBuffer()
    for i in range(10):
        buf.push_final(f"msg{i}", ts=1_700_000_000.0 + i)
    frame = buf.render(width_body_chars=20, height_lines=5)
    assert len(frame.lines) <= 5
    # "msg9" (the last push) must still be present.
    assert any("msg9" in line.text for line in frame.lines)
    # "msg0" was dropped from the top.
    assert not any("msg0" in line.text for line in frame.lines)
    # A leading separator must never be emitted after truncation.
    assert frame.lines[0].kind != "separator"


def test_history_bounded_by_max_messages():
    buf = TextBuffer(max_messages=2)
    buf.push_final("a", ts=1.0)
    buf.push_final("b", ts=2.0)
    buf.push_final("c", ts=3.0)
    frame = buf.render(width_body_chars=20, height_lines=10)
    texts = _texts(frame.lines)
    assert "a" not in texts
    assert "b" in texts
    assert "c" in texts


def test_last_final_ts_tracked():
    buf = TextBuffer()
    assert buf.render(20, 5).last_final_ts is None
    buf.push_final("x", ts=1_700_000_042.0)
    assert buf.render(20, 5).last_final_ts == 1_700_000_042.0


def test_very_long_word_is_hard_broken():
    buf = TextBuffer()
    buf.push_final("supercalifragilisticexpialidocious", ts=1_700_000_000.0)
    frame = buf.render(width_body_chars=8, height_lines=10)
    for line in frame.lines:
        if line.kind == "final":
            assert len(line.text) <= 8
