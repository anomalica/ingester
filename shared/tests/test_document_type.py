from document_type import (
    DOCUMENT_TYPES,
    classify_av,
    classify_text,
    derive_document_type,
    normalise_file_format,
)


def test_av_titles_that_state_their_form():
    assert classify_av("... - DEBRIEFED ep. 45") == "interview"
    assert classify_av('"Victor" Interviewed by Art Bell 3/8') == "interview"
    assert (
        classify_av("Secrets of the UFOs | Full Documentary | 7NEWS") == "documentary"
    )
    assert classify_av("PROJECT: STARGATE (Documentary 2 of 3)") == "documentary"
    assert classify_av("The Evolution of AAWSAP | Bigelow Podcast, Ep. 2") == "podcast"
    assert classify_av("Whitley Strieber | ep. 96") == "podcast"
    assert (
        classify_av("LIVE: James Fox UFO press conference on Varginha") == "broadcast"
    )


def test_av_no_stated_form_is_absent():
    # A neutral guess would assert false evidence weight; absence invites a human.
    assert classify_av("Meet the Navy Scientist With UFO Patents") is None
    assert classify_av("NASA-UAP-D013, Mercury Atlas 7, May 24, 1962") is None
    assert classify_av("") is None


def test_debriefed_wins_over_episode_number():
    # A DEBRIEFED title also carries "ep. N"; the named series (interview) must win,
    # so precedence is by list order, not by which pattern happens to match.
    assert classify_av("The Clearest Video of a Tic Tac UAP! - DEBRIEFED ep. 6") == (
        "interview"
    )


def test_text_titles_that_state_their_form():
    assert classify_text("UAP Sighting Report") == "report"
    assert (
        classify_text("Report on the Historical Record of U.S. Government") == "report"
    )
    assert classify_text("Conference Report: Cultural and Linguistic Advancement") == (
        "report"
    )
    assert classify_text("Statement to Congress") == "statement"
    assert classify_text("Apollo 12 Air-to-Ground Voice Transcription") == "transcript"
    assert classify_text("Correspondence regarding UFO sightings at Los Alamos") == (
        "letter"
    )
    assert classify_text("AATIP briefing slide 9") == "slide"


def test_form_beats_report_when_both_present():
    # "Range Fouler Reporting Form": "Form" is the noun; "Reporting" must NOT trip
    # the report pattern (word boundary), and form is checked first anyway.
    assert classify_text("Range Fouler Reporting Form") == "form"


def test_report_as_a_verb_states_no_form():
    # The anchor rule: a form-word used as a verb names no document. This is the
    # part someone will try to loosen later, so it is pinned by a test.
    assert classify_text("Tajik Air Pilots Report Unidentified Flying Object") is None


def test_a_paper_states_its_topic_not_its_form():
    assert classify_text("Gravity as a zero-point-fluctuation force") is None
    assert classify_text("On Propagation of Light in the Vacuum Between Plates") is None


def test_derive_routes_by_source_type():
    assert derive_document_type("video", "... DEBRIEFED ep. 3") == "interview"
    assert derive_document_type("audio", "NASA-UAP-D013, Mercury Atlas 7") is None
    assert derive_document_type("pdf", "UAP Sighting Report") == "report"
    assert derive_document_type("image", "AATIP briefing slide 9") == "slide"
    # A container states nothing about its contents.
    assert derive_document_type("ebook", "Communion") is None
    # Web email is header-derived in the handler, not from the title.
    assert derive_document_type("web", "Some News Headline") is None


def test_every_derivable_value_is_in_the_closed_set():
    titles = [
        "DEBRIEFED ep. 1",
        "Full Documentary",
        "Bigelow Podcast Ep. 2",
        "press conference",
        "Incident Report",
        "Statement to Congress",
        "Debrief Form",
        "Voice Transcription",
        "Correspondence about X",
        "briefing slide 9",
    ]
    for t in titles:
        for st in ("video", "pdf"):
            dt = derive_document_type(st, t)
            assert dt is None or dt in DOCUMENT_TYPES


def test_file_format_normalisation():
    assert normalise_file_format("ogg") == "opus"
    assert normalise_file_format("opus") == "opus"
    assert normalise_file_format(".JPEG") == "jpg"
    assert normalise_file_format("htm") == "html"
    assert normalise_file_format("pdf") == "pdf"
    assert normalise_file_format("epub") == "epub"
    assert normalise_file_format(None) is None
    assert normalise_file_format("") is None
