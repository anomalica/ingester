"""Post-transcription casing normalisation.

The load-bearing property is SAFETY: it must fix "i saw a ufo" -> "I saw a UFO"
without ever damaging sound-alike or substring text. The adversarial cases below
pin exactly that - a term inside a larger word, an ambiguous word deliberately not
in the list, a lone "i" that isn't the pronoun.
"""

from casing import build_caser, default_caser, load_rules


def _c(terms):
    """A caser over an explicit list of canonical terms."""
    return build_caser({t.lower(): t for t in terms})


# --- core behaviour -----------------------------------------------------------


def test_single_word_acronym_and_pronoun():
    assert _c(["UFO", "I"])("i saw a ufo") == "I saw a UFO"


def test_plural_is_its_own_canonical_form():
    caser = _c(["UFO", "UFOs"])
    assert caser("two ufos and one ufo") == "two UFOs and one UFO"


def test_multi_word_phrase():
    assert _c(["Air Force"])("the air force said") == "the Air Force said"


def test_case_insensitive_normalisation():
    caser = _c(["UFO", "NASA"])
    assert caser("a Ufo, a Nasa report, a UFO") == "a UFO, a NASA report, a UFO"


def test_pronoun_survives_in_a_contraction():
    assert _c(["I"])("i'm sure i saw it") == "I'm sure I saw it"


def test_longest_term_wins_over_its_first_word():
    caser = _c(["Air", "Air Force"])
    assert caser("the air force base") == "the Air Force base"


# --- SAFETY: never damage sound-alike / substring text ------------------------


def test_a_term_inside_a_larger_word_is_untouched():
    # \b boundaries: "ufo" inside "ufology" must not be capitalised.
    assert _c(["UFO"])("ufology is the study") == "ufology is the study"


def test_an_ambiguous_word_absent_from_the_list_is_untouched():
    # 'us' and 'may' are deliberately NOT terms; only 'ufo' fires.
    caser = _c(["UFO"])
    assert caser("give it to us, it may be a ufo") == "give it to us, it may be a UFO"


def test_pronoun_i_only_matches_a_standalone_word():
    assert _c(["I"])("the aircraft is big") == "the aircraft is big"


def test_empty_rules_is_the_identity():
    assert build_caser({})("i saw a ufo") == "i saw a ufo"


# --- the bundled list ---------------------------------------------------------


def test_bundled_terms_load_and_candidates_are_excluded():
    rules = load_rules()
    assert rules["ufo"] == "UFO"
    assert rules["ufos"] == "UFOs"
    assert rules["air force"] == "Air Force"
    assert rules["i"] == "I"
    assert rules["nasa"] == "NASA"
    assert rules["roswell"] == "Roswell"
    # The candidates: block must NOT be loaded - these would corrupt prose.
    for ambiguous in ("us", "may", "march", "mars", "navy", "moon", "it"):
        assert ambiguous not in rules, f"{ambiguous!r} is ambiguous and must not load"


def test_default_caser_end_to_end_and_leaves_ambiguous_prose_alone():
    caser = default_caser()
    assert (
        caser("i think the air force tracked ufos near roswell")
        == "I think the Air Force tracked UFOs near Roswell"
    )
    # The dangerous look-alikes stay exactly as spoken.
    assert caser("it may be one of us") == "it may be one of us"


def test_case_only_transform_keeps_token_count():
    caser = default_caser()
    for text in ["i saw a ufo", "the air force near roswell", "two ufos and a uap"]:
        assert len(caser(text).split()) == len(text.split())
