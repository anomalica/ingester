from publisher import canonical_publisher, strip_site_suffix


def test_a_tagline_is_not_part_of_the_name():
    """23 records carry this one. The masthead is the publication; the rest is
    marketing that turns one publisher into a new node per spelling."""
    assert (
        canonical_publisher(
            "Liberation Times | Reimagining Old News",
            "https://www.liberationtimes.com/articles/x",
        )
        == "Liberation Times"
    )
    assert (
        canonical_publisher(
            "The Black Vault - Discover the Truth", "https://www.theblackvault.com/x"
        )
        == "The Black Vault"
    )


def test_a_known_host_beats_a_mangled_site_name():
    """`Nytimes` is a title-cased domain slug, not a masthead; `wikileaks.org` and
    `space.com` are hostnames the extractor gave up on."""
    assert (
        canonical_publisher("Nytimes", "https://www.nytimes.com/2017/12/16/x.html")
        == "The New York Times"
    )
    assert (
        canonical_publisher("wikileaks.org", "https://wikileaks.org/x") == "WikiLeaks"
    )
    assert canonical_publisher("space.com", "https://www.space.com/x") == "Space.com"


def test_an_unknown_site_name_is_cleaned_not_replaced():
    """Inventing a masthead from a domain is how `Nytimes` happened. A name that is
    already right passes through; whitespace damage is repaired."""
    assert canonical_publisher("The Debrief", "https://thedebrief.org/x") == (
        "The Debrief"
    )
    assert canonical_publisher("raefos:AetherNet  TV", None) == "raefos:AetherNet TV"
    assert canonical_publisher("", "https://unknown.example/x") == ""
    assert canonical_publisher(None, None) == ""


def test_a_hyphenated_name_is_not_split():
    """The separators require surrounding spaces precisely so this survives."""
    assert canonical_publisher("Sci-Fi Weekly", "https://scifi.example/x") == (
        "Sci-Fi Weekly"
    )


def test_the_site_suffix_comes_off_the_title():
    title = (
        "Lue Elizondo: There's No Going Back on UFO Disclosure "
        "— Liberation Times | Reimagining Old News"
    )
    assert (
        strip_site_suffix(
            title,
            "Liberation Times | Reimagining Old News",
            "https://www.liberationtimes.com/articles/x",
        )
        == "Lue Elizondo: There's No Going Back on UFO Disclosure"
    )


def test_the_new_york_times_published_year_comes_off():
    """Site chrome, not a subtitle - it marks the archive page, not the article."""
    title = (
        "Glowing Auras and 'Black Money': The Pentagon's Mysterious U.F.O. Program "
        "(Published 2017)"
    )
    assert strip_site_suffix(title, "Nytimes", "https://www.nytimes.com/x").endswith(
        "Mysterious U.F.O. Program"
    )


def test_a_title_that_is_only_chrome_is_left_alone():
    """One record's title is the site name and nothing else. Stripping would leave
    an empty title - a worse defect than the one being fixed, and one a reviewer
    can no longer see."""
    name = "Liberation Times | Reimagining Old News"
    assert strip_site_suffix(name, name, "https://www.liberationtimes.com/") == name


def test_a_real_subtitle_after_a_dash_survives():
    """Only a tail that IS the site's name comes off, so ordinary punctuation in a
    headline is safe."""
    title = "The Missing General - Neil McCasland and an Unfolding Mystery"
    assert (
        strip_site_suffix(
            title, "Liberation Times", "https://www.liberationtimes.com/x"
        )
        == title
    )


def test_a_bare_hostname_tail_comes_off_too():
    assert (
        strip_site_suffix(
            "Some Article - space.com", "space.com", "https://www.space.com/x"
        )
        == "Some Article"
    )
