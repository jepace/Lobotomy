#!/bin/sh
# Reviewed from find_duplicate_pages.py output. Largest page survives in each group, which
# is what that tool suggests and is right here — the abbreviation-titled page is usually the
# fuller one, and its title becomes an alias of the survivor either way.
#
# DELIBERATELY EXCLUDED, reviewed as NOT duplicates:
#   maga / maga-inc                  a movement and a super PAC
#   black-rock-city / -llc           a city and the company that runs the event
#   shell / shell-company            an oil major and a concept
#   pg-e / pge-corporation           a utility and its holding company
#   korea-gas-corporation / -corp    check for a subsidiary first
#   rural-development concept/entity check: a USDA agency vs the general concept
#   major-oak entity / concept       a tree, and a page made because the slug was taken
#
# Run with --dry-run first. Drop --dry-run when it reads right.
set -e
DRY="$1"

m() { survivor="$1"; shift; python3 tools/merge_page.py "$@" --into "$survivor" --carry $DRY; }

m wiki/entities/acme-corporation.md                        wiki/entities/acme-company.md
m wiki/entities/aipac.md                                   wiki/entities/american-israel-public-affairs-committee.md
m wiki/concepts/the-antichrist.md                          wiki/concepts/antichrist.md
m wiki/concepts/babyfire.md                                wiki/entities/babyfire.md
m wiki/concepts/ceqa.md                                    wiki/concepts/california-environmental-quality-act.md
m wiki/concepts/california-housing-and-homelessness-agency.md wiki/entities/california-housing-and-homelessness-agency.md
m wiki/entities/claudia-sheinbaum.md                       wiki/entities/claudia-sheinbaum-pardo.md
m wiki/entities/department-of-homeland-security.md         wiki/entities/dhs.md
m wiki/concepts/dei.md                                     wiki/concepts/diversity-equity-and-inclusion.md
m wiki/entities/epa.md                                     wiki/concepts/environmental-protection-agency.md
m wiki/entities/food-and-drug-administration.md            wiki/entities/fda.md
m wiki/entities/globe-and-mail.md                          wiki/entities/the-globe-and-mail.md
m wiki/entities/internal-revenue-service.md                wiki/entities/irs.md
m wiki/entities/k-and-d-landscaping.md                     wiki/entities/kd-landscaping.md
m wiki/entities/mitsubishi-corporation.md                  wiki/entities/mitsubishi-corp.md
m wiki/entities/noaa.md                                    wiki/entities/national-oceanic-and-atmospheric-administration.md
m wiki/entities/national-security-agency.md                wiki/entities/nsa.md
m wiki/entities/the-new-york-times.md                      wiki/entities/new-york-times.md
m wiki/entities/nerc.md                                    wiki/entities/north-american-electric-reliability-corporation.md
m wiki/entities/rationalist-community.md                   wiki/concepts/rationalist-community.md
m wiki/entities/rcmp.md                                    wiki/entities/royal-canadian-mounted-police.md
m wiki/entities/supreme-court.md                           wiki/entities/supreme-court-of-the-united-states.md
m wiki/concepts/tool-call-spoofing.md                      wiki/concepts/tool-call-spoofing-concept.md
m wiki/entities/us-department-of-the-treasury.md            wiki/entities/us-treasury.md
m wiki/entities/ice.md                                     wiki/entities/ice-agency.md
m wiki/entities/the-wall-street-journal.md                 wiki/entities/wall-street-journal.md
m wiki/entities/the-washington-post.md                      wiki/entities/washington-post.md

# Five pages, one command — this is what taking several losers is for.
m wiki/entities/pacific-gas-and-electric-company.md \
  wiki/entities/pge.md wiki/entities/pacific-gas-electric-co.md \
  wiki/entities/pacific-gas-and-electric.md wiki/entities/pacific-gas-electric.md

echo
echo "Then: python3 tools/relink.py   # re-link bare prose under the survivors' new aliases"
echo
echo "Afterwards, eight survivors still carry a parenthetical abbreviation in their TITLE:"
echo "  AIPAC, CEQA, DEI, EPA, NOAA, NERC, ICE, RCMP"
echo "That shape is what created most of these duplicates in the first place. Nobody writes"
echo "'Environmental Protection Agency (EPA)' mid-sentence, so the autolinker never matches"
echo "the title, links the other page instead, and both pages accumulate. The abbreviation"
echo "belongs in aliases:, which is where --carry has now put the loser's name anyway:"
echo "  python3 tools/rename_page.py wiki/entities/epa.md --title 'Environmental Protection Agency'"
