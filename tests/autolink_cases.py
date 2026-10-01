"""Characterization corpus for the autolinker.

Each case: (name, [(title, aliases, no_autolink)], target_subdir, body).
Run against the current implementation to capture a baseline, then against the optimized
one — the outputs must be byte-identical.
"""

CASES = [
    ("simple", [("Monterey County", [], False)], "entities",
     "The case was heard in Monterey County last year."),

    ("all_occurrences", [("Meta", [], False)], "entities",
     "Meta said X. Later, Meta said Y. Meta again."),

    ("existing_link_untouched", [("Meta", [], False)], "entities",
     "See [Meta](../entities/meta.md) for details, and Meta again."),

    ("no_nesting_other_link", [("Meta", [], False)], "entities",
     "Read [about Meta here](https://example.com/meta) please."),

    ("partial_link_upgrade", [("CASA of Monterey County", [], False)], "entities",
     "CASA of [Monterey County](../entities/monterey-county.md) filed suit."),

    ("overlapping_titles", [("Monterey County", [], False),
                            ("CASA of Monterey County", [], False)], "entities",
     "CASA of Monterey County filed in Monterey County court."),

    ("overlapping_reverse", [("CASA of Monterey County", [], False),
                             ("Monterey County", [], False)], "entities",
     "CASA of Monterey County filed in Monterey County court."),

    ("case_insensitive", [("Monterey County", [], False)], "entities",
     "monterey county and MONTEREY COUNTY and Monterey County."),

    ("word_boundary", [("Meta", [], False)], "entities",
     "Metadata is not Meta. Metaphor. Meta's thing. premeta."),

    ("headings_skipped", [("Meta", [], False)], "entities",
     "# Meta\n\n## About Meta\n\nMeta did a thing.\n\n### Meta again\n\nAnd Meta."),

    ("regex_special_chars", [("PG&E", [], False), ("C++", [], False),
                             ("AT&T", [], False)], "entities",
     "PG&E and C++ and AT&T were named. PG&E again."),

    # Three names for one page in ONE section now yields ONE link: the once-per-section
    # budget belongs to the page, not to the name, so an alias cannot spend a second
    # mention. Longest-first means the fullest name takes it.
    ("aliases", [("Pacific Gas and Electric Company", ["PG&E", "the utility"], False)],
     "entities",
     "PG&E did it. Pacific Gas and Electric Company confirmed. the utility agreed."),

    # ...which is why this one exists: the case above no longer shows that the aliases
    # MATCH at all, only that they do not double-link. One alias per section restores that
    # coverage — all three link, because each section gets its own mention.
    ("aliases_one_per_section",
     [("Pacific Gas and Electric Company", ["PG&E", "the utility"], False)], "entities",
     "## One\n\nPG&E did it.\n\n## Two\n\nPacific Gas and Electric Company "
     "confirmed.\n\n## Three\n\nthe utility agreed."),

    ("no_autolink_excluded", [("Meta", [], True), ("Canada", [], False)], "entities",
     "Meta and Canada were mentioned."),

    ("self_page_excluded", [("Target Page", [], False), ("Meta", [], False)], "entities",
     "Target Page should not link to itself but Meta should link."),

    ("punctuation_adjacent", [("Meta", [], False)], "entities",
     "Meta. Meta, Meta; (Meta) [Meta] \"Meta\" 'Meta' Meta!"),

    ("multiword_spacing", [("Mark Carney", [], False)], "entities",
     "Mark Carney spoke. Mark  Carney with two spaces. Mark\tCarney with tab."),

    ("across_lines", [("Mark Carney", [], False)], "entities",
     "The prime minister Mark\nCarney said something."),

    ("title_in_url", [("Meta", [], False)], "entities",
     "See https://example.com/meta/page and Meta here."),

    ("title_inside_link_text", [("Monterey County", [], False)], "entities",
     "[The Monterey County Herald](https://example.com) reported it."),

    ("from_sources_subdir", [("Meta", [], False)], "sources",
     "Meta was mentioned in this source."),

    ("from_synthesis_subdir", [("Meta", [], False)], "synthesis",
     "Meta appears in this synthesis."),

    ("empty_body", [("Meta", [], False)], "entities", ""),

    ("frontmatter_untouched", [("Meta", [], False)], "entities",
     "Body mentions Meta once."),

    ("adjacent_links", [("Meta", [], False), ("Canada", [], False)], "entities",
     "[Meta](../entities/meta.md)[Canada](../entities/canada.md) and Meta Canada."),

    ("three_word_partial", [("Bank of Canada", [], False)], "entities",
     "Bank of [Canada](../entities/canada.md) raised rates. "
     "[Bank](../entities/bank.md) of Canada too."),

    ("repeated_partial", [("CASA of Monterey County", [], False)], "entities",
     "CASA of [Monterey County](../x.md) and CASA of [Monterey County](../y.md)."),

    ("title_is_substring_word", [("Canada", [], False), ("Canada Post", [], False)],
     "entities", "Canada Post delivers in Canada."),

    ("hyphenated", [("Canada-US Relations", [], False)], "entities",
     "Canada-US Relations worsened. canada-us relations too."),

    ("numeric_title", [("2026 Midterms", [], False)], "entities",
     "The 2026 Midterms approach."),

    ("apostrophe_title", [("Moody's", [], False)], "entities",
     "Moody's downgraded it. Moody's again."),

    # Code is not prose. Reported as `container.exe` coming out as
    # `[container](../concepts/container.md).exe` inside the backticks.
    ("inline_code_span", [("Container", [], False)], "entities",
     "Native CLI (`wslc.exe` / `container.exe`) ships. A container is a thing."),

    ("fenced_block", [("Docker", [], False), ("Python", [], False)], "entities",
     "Install it.\n\n```sh\ndocker run --rm python:3\n```\n\nThen docker works."),

    ("tilde_fence", [("Docker", [], False)], "entities",
     "Install.\n\n~~~sh\ndocker run\n~~~\n\nThen docker works."),

    ("backticks_inside_a_fence_do_not_close_it", [("Docker", [], False)], "entities",
     "Try:\n\n~~~\ndocker ` tick\ndocker again\n~~~\n\nAnd docker after."),

    ("hash_in_a_fence_is_not_a_heading", [("Docker", [], False)], "entities",
     "Steps:\n\n```sh\n# install docker\ndocker run\n```\n\nThen docker once."),

    ("html_code_tag", [("Python", [], False)], "entities",
     "A <code>python</code> tag, then python in prose."),

    # Already on disk: prevention alone freezes it, since group 1 protects a link once it
    # is inside a span. These flatten back to text.
    ("heals_link_inside_code_span", [("Container", [], False)], "entities",
     "CLI (`[container](../concepts/container.md).exe`) ships."),

    ("heals_link_inside_fence", [("Docker", [], False)], "entities",
     "Run:\n\n```sh\n[docker](../concepts/docker.md) run --rm\n```\n"),

    # ...but a page documenting markdown keeps its example exactly as written.
    ("markdown_example_in_code_is_left_alone", [("Example", [], False)], "entities",
     "Write `[label](https://example.com)` for a link. An Example follows."),

    ("double_backtick_span", [("Docker", [], False)], "entities",
     "Use ``docker ` ps`` carefully. Then docker in prose."),

    # A comma in a TITLE is optional in the text. The failure was not a missed link but a
    # WRONG one: the long title did not match, so the shorter parent did, and a sentence
    # about the campus linked to the whole system.
    ("comma_in_title_optional_in_prose",
     [("University of California", [], False),
      ("University of California, Merced", [], False)], "entities",
     "Researchers at the University of California Merced published it."),

    ("comma_in_title_matches_with_the_comma",
     [("University of California", [], False),
      ("University of California, Merced", [], False)], "entities",
     "Researchers at the University of California, Merced published it."),

    ("parent_still_links_when_the_sentence_is_about_it",
     [("University of California", [], False),
      ("University of California, Merced", [], False)], "entities",
     "The University of California, which runs ten campuses, grew."),

    # The direction NOT taken: a space in a title must not match a comma in prose, or
    # "attended by Smith, Johnson and Lee" links two names as one person.
    ("space_in_title_does_not_match_a_comma_in_prose",
     [("Smith Johnson", [], False)], "entities",
     "Attended by Smith, Johnson and Lee."),

    ("campus_with_no_page_of_its_own",
     [("University of California", [], False)], "entities",
     "A grant went to the University of California, Santa Cruz last year."),
]
