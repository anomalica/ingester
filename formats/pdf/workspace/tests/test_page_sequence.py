from ingest_pdf import _impossible_page_sequence, _resequence_pages_sequential


def test_marker_past_the_last_page_is_impossible():
    err = _impossible_page_sequence("file_page: 5\nfile_page: 150", 116)
    assert err and "150" in err and "116" in err


def test_repeated_page_number_is_impossible():
    err = _impossible_page_sequence("file_page: 1\nfile_page: 1\nfile_page: 2", 3)
    assert err and "repeat" in err


def test_a_valid_complete_sequence_passes():
    assert (
        _impossible_page_sequence("file_page: 1\nfile_page: 2\nfile_page: 3", 3) is None
    )


def test_a_shortfall_is_not_impossible():
    # Fewer markers than pages can be a legitimate blank page, so it must NOT hard
    # block - it is a warning-and-repair case upstream, not corruption.
    assert _impossible_page_sequence("file_page: 1\nfile_page: 2", 5) is None


def test_no_markers_is_not_impossible():
    assert _impossible_page_sequence("no markers here", 5) is None


def test_resequence_then_the_gate_is_clean():
    # The Sandia shape: complete but misnumbered (chunk-offset double-count). After
    # resequencing it is 1..N, which the impossible-gate then passes.
    misnumbered = "file_page: 76\nfile_page: 77\nfile_page: 63"
    fixed, changed = _resequence_pages_sequential(misnumbered, 3)
    assert changed
    assert _impossible_page_sequence(fixed, 3) is None


# Markers come in two notations: inline `<!-- file_page: 12 -->` and a block form
# with file_page on its own line beside printed_page. A matcher keyed on the inline
# punctuation is blind to the block form - the same confident-wrong-answer shape as
# the bug itself - so the page logic must key on `file_page: N`, not the comment.

_BLOCK = """<!--
file_page: 1
printed_page: 4
-->
one
<!--
file_page: 2
printed_page: 5
-->
two"""


def test_block_notation_is_seen_by_the_gate():
    assert _impossible_page_sequence(_BLOCK, 2) is None
    assert _impossible_page_sequence(_BLOCK.replace("file_page: 2", "file_page: 99"), 2)


def test_mixed_notation_resequences_and_leaves_printed_page_alone():
    mixed = (
        "<!-- file_page: 76 -->\nA\n"
        "<!--\nfile_page: 77\nprinted_page: 5\n-->\nB\n"
        "<!-- file_page: 63 -->\nC"
    )
    fixed, changed = _resequence_pages_sequential(mixed, 3)
    assert changed
    import re

    assert re.findall(r"file_page: (\d+)", fixed) == ["1", "2", "3"]
    assert "printed_page: 5" in fixed  # printed_page is not a file_page marker
    assert _impossible_page_sequence(fixed, 3) is None
