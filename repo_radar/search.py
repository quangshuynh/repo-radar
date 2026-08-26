"""search query parsing that separates a language constraint from topical terms"""

from __future__ import annotations

import re

from .models import SearchQuery

DEFAULT_LANGUAGE = "Python"

# Search tokens mapped to the exact language names GitHub reports as a repository's top
# language. Only the names are canonical; the keys include the abbreviations people
# actually type. This is deliberately a common-language table rather than the full
# Linguist list: every extra rare name is another English word that could be swallowed
# out of a topical query.
LANGUAGE_ALIASES: dict[str, str] = {
    "assembly": "Assembly",
    "bash": "Shell",
    "c": "C",
    "c#": "C#",
    "c++": "C++",
    "clojure": "Clojure",
    "cplusplus": "C++",
    "cpp": "C++",
    "crystal": "Crystal",
    "csharp": "C#",
    "css": "CSS",
    "d": "D",
    "dart": "Dart",
    "elixir": "Elixir",
    "elm": "Elm",
    "erlang": "Erlang",
    "f#": "F#",
    "fortran": "Fortran",
    "fsharp": "F#",
    "go": "Go",
    "golang": "Go",
    "groovy": "Groovy",
    "haskell": "Haskell",
    "haxe": "Haxe",
    "html": "HTML",
    "java": "Java",
    "javascript": "JavaScript",
    "js": "JavaScript",
    "julia": "Julia",
    "jupyter": "Jupyter Notebook",
    "kotlin": "Kotlin",
    "kt": "Kotlin",
    "lua": "Lua",
    "matlab": "MATLAB",
    "nim": "Nim",
    "objc": "Objective-C",
    "objective-c": "Objective-C",
    "ocaml": "OCaml",
    "perl": "Perl",
    "php": "PHP",
    "powershell": "PowerShell",
    "py": "Python",
    "python": "Python",
    "r": "R",
    "racket": "Racket",
    "rb": "Ruby",
    "rs": "Rust",
    "ruby": "Ruby",
    "rust": "Rust",
    "scala": "Scala",
    "scheme": "Scheme",
    "shell": "Shell",
    "solidity": "Solidity",
    "sql": "SQL",
    "svelte": "Svelte",
    "swift": "Swift",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "vue": "Vue",
    "zig": "Zig",
}

# Language names that are also ordinary English words. "go api" clearly requests Go, but
# "libraries that go fast" does not, so these are only honored in the leading position
# where a user states the language they want.
COMMON_WORD_LANGUAGES = frozenset({"assembly", "bash", "elm", "go", "shell", "swift"})

_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9+#.-]*")


def _tokenize(query: str) -> list[str]:
    """
    split a search query into normalized tokens
    :param query: raw user search query
    :returns: lowercase tokens preserving language punctuation
    """
    return [token.strip(".-") for token in _TOKEN_PATTERN.findall(query.lower()) if token.strip(".-")]


def _is_leading_only(token: str) -> bool:
    """
    determine whether a language token is too ambiguous to match mid query
    :param token: normalized search token
    :returns: whether the token only counts as a language in the leading position
    """
    return token in COMMON_WORD_LANGUAGES or (len(token) <= 2 and token.isalpha())


def _language_position(tokens: list[str]) -> int | None:
    """
    locate the first token that explicitly names a programming language
    :param tokens: normalized search tokens
    :returns: index of the language token or none
    """
    for index, token in enumerate(tokens):
        if token in LANGUAGE_ALIASES and (index == 0 or not _is_leading_only(token)):
            return index
    return None


def parse_search_query(query: str) -> SearchQuery:
    """
    split a search query into a primary language constraint and the remaining topical terms
    :param query: raw user search query
    :returns: parsed search intent defaulting to the Python primary language
    """
    tokens = _tokenize(query)
    position = _language_position(tokens)
    if position is None:
        return SearchQuery(language=DEFAULT_LANGUAGE, terms=tuple(tokens))
    return SearchQuery(
        language=LANGUAGE_ALIASES[tokens[position]],
        terms=tuple(tokens[:position] + tokens[position + 1 :]),
        explicit_language=True,
    )
