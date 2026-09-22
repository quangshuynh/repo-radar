"""context aware disambiguation of preference terms that name two unrelated things

Preference matching is string equality: a profile weight for `homebrew` credits every
candidate carrying the token `homebrew`. That is correct for terms with one meaning and
wrong for terms that name two unrelated ecosystems. `homebrew` is the motivating case — it
means both console homebrew development (Nintendo Switch, 3DS, Wii, Vita) and the macOS
package manager, so a profile built from Switch homebrew repositories credits macOS tap and
formula issues with the full weight of its strongest topic.

The mechanism here is deliberately narrow, and it is disambiguation rather than filtering:

- only the terms in `AMBIGUOUS_TERMS` are affected; every other term is looked up as before;
- a sense is assigned only from *unambiguous* corroborating markers, never from the
  ambiguous term itself, so resolution cannot be circular or self-confirming;
- when both senses are corroborated equally the term is left undetermined, so genuinely
  ambiguous data never produces a confident classification;
- a mismatch withholds the term's contribution instead of subtracting a penalty, so a
  candidate is never scored below an otherwise identical one that never used the term.

The last two points are what keep this from behaving like a blacklist. A macOS Homebrew
repository loses nothing for a user whose own profile reads as macOS Homebrew, and a
candidate whose text says only "homebrew" scores exactly what it scored before.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

from .models import PreferenceProfile

CONSOLE_HOMEBREW = "console homebrew"
MACOS_PACKAGE_MANAGER = "macos package manager"

# Markers are terms that belong to one sense and carry no plausible reading in the other.
# Ambiguous words are deliberately absent: `homebrew`, `brew`, and bare `switch` are what is
# being disambiguated, so admitting them as evidence would let a term corroborate itself.
HOMEBREW_SENSES: dict[str, frozenset[str]] = {
    CONSOLE_HOMEBREW: frozenset(
        {
            "3ds",
            "atmosphere-nx",
            "cfw",
            "console-homebrew",
            "custom-firmware",
            "devkita64",
            "devkitarm",
            "devkitpro",
            "gamecube",
            "hbmenu",
            "hekate",
            "homebrew-launcher",
            "libnx",
            "nintendo",
            "nintendo-3ds",
            "nintendo-switch",
            "nro",
            "nsp",
            "psp",
            "ps-vita",
            "switch-homebrew",
            "sysmodule",
            "wii",
            "wiiu",
        }
    ),
    MACOS_PACKAGE_MANAGER: frozenset(
        {
            "brewfile",
            "cask",
            "casks",
            "darwin",
            "formula",
            "formulae",
            "homebrew-cask",
            "homebrew-core",
            "homebrew-tap",
            "macos",
            "mac-os",
            "macports",
            "os-x",
            "osx",
            "package-manager",
            "tap",
            "taps",
        }
    ),
}

# Terms whose preference weight depends on which sense a candidate means. `brew` shares the
# homebrew senses because it is the same collision in shorter form.
AMBIGUOUS_TERMS: dict[str, dict[str, frozenset[str]]] = {
    "homebrew": HOMEBREW_SENSES,
    "brew": HOMEBREW_SENSES,
}

_SEPARATOR_PATTERN = re.compile(r"[\s_/]+")

# A profile carries hundreds of signals and its senses are re-read for every candidate, so
# the same few hundred strings are normalized over and over within a single ranking run.
# Memoizing a pure string to string function keeps that from dominating scoring cost; it
# changes no result.
NORMALIZATION_CACHE_SIZE = 4096


@lru_cache(maxsize=NORMALIZATION_CACHE_SIZE)
def normalize_term(value: str) -> str:
    """
    reduce one signal name to the canonical spelling markers are written in
    :param value: topic, label, language, or extracted keyword
    :returns: lowercase term with separators collapsed to single hyphens
    """
    return _SEPARATOR_PATTERN.sub("-", value.strip().lower()).strip("-")


def sense_context(terms: Iterable[str] = (), phrases: Iterable[Iterable[str]] = ()) -> frozenset[str]:
    """
    collect the normalized evidence a candidate's sense may be read from

    Adjacent word pairs are read from `phrases` as well as the words themselves, because a
    two word marker such as `nintendo switch` or `package manager` arrives from free text as
    two separate tokens while GitHub topics carry it already hyphenated. Only ordered word
    sequences qualify: adjacency in a description means the words belong together, adjacency
    in a topic list or a weight-sorted profile means nothing.
    :param terms: standalone terms such as topics, labels, or profile signal names
    :param phrases: ordered word sequences such as description, title, or body keywords
    :returns: normalized terms and phrase bigrams
    """
    context = {term for term in (normalize_term(value) for value in terms) if term}
    for phrase in phrases:
        words = [word for word in (normalize_term(value) for value in phrase) if word]
        context.update(words)
        context.update(f"{left}-{right}" for left, right in zip(words, words[1:], strict=False))
    return frozenset(context)


def resolve_sense(term: str, context: frozenset[str]) -> str | None:
    """
    read which sense of an ambiguous term a body of evidence supports

    The sense corroborated by strictly more distinct markers wins. Equal support, including
    no support at all, resolves to nothing: the evidence is genuinely ambiguous and is
    reported as such rather than resolved by an arbitrary preference between senses.
    :param term: candidate term in any spelling
    :param context: normalized evidence from `sense_context`
    :returns: sense name, or none when the term is unambiguous or the evidence is not decisive
    """
    senses = AMBIGUOUS_TERMS.get(normalize_term(term))
    if not senses:
        return None
    support_by_sense = ((len(markers & context), name) for name, markers in senses.items())
    ranked = sorted(support_by_sense, key=lambda item: (-item[0], item[1]))
    support, name = ranked[0]
    if support == 0 or (len(ranked) > 1 and ranked[1][0] == support):
        return None
    return name


def profile_senses(profile: PreferenceProfile) -> dict[str, str]:
    """
    read which sense of each ambiguous term the user's own profile means
    :param profile: user preference profile
    :returns: resolved sense per ambiguous term, omitting terms the profile leaves ambiguous
    """
    context = sense_context((*profile.topics, *profile.keywords, *profile.languages))
    resolved = ((term, resolve_sense(term, context)) for term in AMBIGUOUS_TERMS)
    return {term: sense for term, sense in resolved if sense}


def preference_applies(term: str, senses: dict[str, str], context: frozenset[str]) -> bool:
    """
    decide whether a profile preference for one term applies to a candidate

    The preference applies unless the term is ambiguous, the profile resolved it to one
    sense, and the candidate's own evidence resolves it to the other. Undetermined evidence
    on either side keeps the preference, so nothing changes for a profile or a candidate that
    says only "homebrew" with no context to read it against.
    :param term: candidate term in any spelling
    :param senses: senses resolved from the profile by `profile_senses`
    :param context: normalized candidate evidence from `sense_context`
    :returns: whether the profile weight for the term should be credited
    """
    wanted = senses.get(normalize_term(term))
    if wanted is None:
        return True
    observed = resolve_sense(term, context)
    return observed is None or observed == wanted


def contextual_weight(term: str, weights: dict[str, float], senses: dict[str, str], context: frozenset[str]) -> float:
    """
    look up one preference weight, withholding it when the candidate means another sense
    :param term: candidate term in any spelling
    :param weights: normalized profile weights keyed by term
    :param senses: senses resolved from the profile by `profile_senses`
    :param context: normalized candidate evidence from `sense_context`
    :returns: profile weight for the term, or zero when the senses disagree
    """
    return weights.get(term, 0.0) if preference_applies(term, senses, context) else 0.0
