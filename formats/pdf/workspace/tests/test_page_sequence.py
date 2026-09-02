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
