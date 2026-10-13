"""Text clean-up shared by every report format."""

import re

_PLACEHOLDER = "\x01"   # stands in for a literal escaped dollar sign while math spans are unwrapped
_MATH_SPAN = re.compile(r"\$([^$\n]+)\$")
_TEXT_COMMAND = re.compile(r"\\(?:text|mathrm|mathbf|textbf|operatorname)\{([^{}]*)\}")
_SYMBOLS = {
    r"\times": "\u00d7", r"\cdot": "\u00b7", r"\approx": "\u2248", r"\pm": "\u00b1", r"\leq": "\u2264",
    r"\geq": "\u2265", r"\rightarrow": "\u2192", r"\%": "%", r"\_": "_", r"\&": "&", r"\,": " ", r"\;": " ",
}


def strip_latex(text: str) -> str:
    """Remove LaTeX markup that models leave in prose and tables.

    ``$\\$457.55$`` becomes ``$457.55``; ``\\text{\\$M}`` becomes ``$M``. Plain prices such
    as "costs $5 and $10" are untouched: a ``$...$`` span is only unwrapped when it holds a
    backslash command.
    """
    if not text or ("\\" not in text):
        return text
    s = text.replace("\\$", _PLACEHOLDER)

    def unwrap(match: "re.Match[str]") -> str:
        inner = match.group(1)
        return inner if ("\\" in inner or _PLACEHOLDER in inner) else match.group(0)

    s = _MATH_SPAN.sub(unwrap, s)
    for _ in range(3):  # nested \text{\mathrm{..}}
        s = _TEXT_COMMAND.sub(r"\1", s)
    for command, symbol in _SYMBOLS.items():
        s = s.replace(command, symbol)
    s = re.sub(r"\\(?:left|right|big|Big)\b\s*", "", s)
    return s.replace(_PLACEHOLDER, "$")
