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

    ("aliases", [("Pacific Gas and Electric Company", ["PG&E", "the utility"], False)],
     "entities",
     "PG&E did it. Pacific Gas and Electric Company confirmed. the utility agreed."),

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
]
