"""Fix up pandoc's LaTeX body for comment-exposure.pdf (stdin -> stdout).

Called by build-comment-exposure.sh. Kept as a file because regexes over
LaTeX backslashes do not survive being quoted inside a shell string.
"""
import re
import sys

FIGURES = "arrival|lag-overlap|adjacent-overlap"


def swap_svg(s: str) -> str:
    # The markdown points at the SVG so the HTML render can use it. Pandoc picks
    # \includesvg off that extension, which LaTeX has no engine for, so swap the
    # command and the path together for the rasterless PDF.
    return re.sub(
        r"\\includesvg\[[^]]*\]\{figures/(" + FIGURES + r")\.svg\}",
        r"\\includegraphics[width=\\linewidth]{figures/\1.pdf}",
        s,
    )


# Column sizing works in points at the tables' \small size (10pt in an 11pt
# article), with Latin Modern's glyph widths at 10pt. A flat per-character
# guess was 20% wide on numbers ("0.721" is 23pt, not 28), so the 11-column
# decile table looked full while half its width sat empty.
GLYPH_PT = {
    **dict.fromkeys("0123456789", 5.0),
    **dict.fromkeys(".,:;'!|`", 2.78),
    **dict.fromkeys("()[]-~/", 3.89),
    " ": 3.33, "%": 8.33, "–": 5.0, "—": 10.0, '"': 5.0,
    **dict.fromkeys("×≤≥−+=<>", 7.78),
    "a": 5.0, "b": 5.56, "c": 4.44, "d": 5.56, "e": 4.44, "f": 3.06, "g": 5.0,
    "h": 5.56, "i": 2.78, "j": 3.06, "k": 5.28, "l": 2.78, "m": 8.33, "n": 5.56,
    "o": 5.0, "p": 5.56, "q": 5.28, "r": 3.92, "s": 3.94, "t": 3.89, "u": 5.56,
    "v": 5.28, "w": 7.22, "x": 5.28, "y": 5.28, "z": 4.44,
}
UPPER_PT = 7.5   # capitals run 6.8 to 9.2; this errs wide
OTHER_PT = 6.0   # anything unlisted: errs wide
SLACK_PT = 3.0   # per cell, so an estimate a little short still fits
SPACE_PT = GLYPH_PT[" "]


def text_pt(text: str) -> float:
    return sum(GLYPH_PT.get(ch, UPPER_PT if ch.isupper() else OTHER_PT) for ch in text)

LINEWIDTH_PT = 469.75  # 8.5in page, 1in margins
TABCOLSEP_PT = 6.0


def plain(cell: str) -> str:
    """A cell's printed text, near enough to count its characters."""
    cell = re.sub(r"\\begin\{minipage\}\[b\]\{\\linewidth\}\\raggedright|\\end\{minipage\}", "", cell)
    cell = re.sub(r"\\[a-zA-Z]+(\[[^]]*\])?", "", cell)
    cell = cell.replace("``", '"').replace("''", '"')
    return " ".join(re.sub(r"[{}\\]", "", cell).split())


def cells(row: str) -> list[str]:
    return [plain(c) for c in re.split(r"(?<!\\)&", row)]


def lines(text: str, width: float) -> int:
    """How many lines a cell takes at a column width, wrapping at spaces."""
    room = width - SLACK_PT
    count, used = 1, 0.0
    for w in text.split():
        size = text_pt(w)
        if used and used + SPACE_PT + size > room:
            count, used = count + 1, size
        else:
            used += (SPACE_PT if used else 0.0) + size
    return count


# A wrapped header costs less than a wrapped body row: two-line headers read
# fine, a number split over two lines does not.
HEADER_WEIGHT = 0.5


def fit_widths(head: list[str], body: list[list[str]]) -> tuple[list[float], bool]:
    """Column widths in points, and whether any body cell still wraps.

    Pandoc divides the line between p{} columns by the dashes under each
    header, which knows nothing of the contents: "146.0 [117.0, 176.0]" got
    split over two lines next to a half-empty column. Size from the cells
    instead, making the table as short as the line allows: every column
    starts at its longest word, and spare width goes, step by step, wherever
    it removes the most wrapped lines per point. Handing it out by any fixed
    rule failed one table or another: shared evenly, every header stayed one
    word short of a line; smallest need first, the corpus table's two prose
    columns were left at four lines a cell beside columns with room to spare.
    """
    n = len(head)
    avail = LINEWIDTH_PT - 2 * (n - 1) * TABCOLSEP_PT
    rows = [(HEADER_WEIGHT, head)] + [(1.0, r) for r in body]
    cols = [[r[i] for _, r in rows] for i in range(n)]

    def height(widths: list[float]) -> float:
        return sum(wt * max(lines(c, w) for c, w in zip(r, widths)) for wt, r in rows)

    def narrowest(text: str, below: int, lo: float) -> float:
        # Smallest width at which text takes fewer than `below` lines.
        hi = text_pt(text) + 2 * SLACK_PT
        while hi - lo > 0.5:
            mid = (lo + hi) / 2
            lo, hi = (lo, mid) if lines(text, mid) < below else (mid, hi)
        return hi

    widths = [max(text_pt(w) + SLACK_PT for c in col for w in (c.split() or [""]))
              for col in cols]
    if sum(widths) > avail:  # even single words do not fit; scale down
        return [w * avail / sum(widths) for w in widths], True
    def moves(widths: list[float]):
        # Widen one column until one of its cells loses a line; or, since a
        # row is only as short as its tallest cell, widen every column that
        # holds a row's tallest cell at once (one at a time gains nothing).
        for i, col in enumerate(cols):
            for c in col:
                k = lines(c, widths[i])
                if k > 1:
                    yield {i: narrowest(c, k, widths[i])}
        for _, r in rows:
            ks = [lines(c, w) for c, w in zip(r, widths)]
            top = max(ks)
            if top > 1:
                yield {i: max(widths[i], narrowest(r[i], top, widths[i]))
                       for i in range(n) if ks[i] == top}

    while True:
        spare = avail - sum(widths)
        current = height(widths)
        best = None  # (gain per point, new widths)
        for move in moves(widths):
            cost = sum(w - widths[i] for i, w in move.items())
            if cost > spare:
                continue
            trial = [move.get(i, w) for i, w in enumerate(widths)]
            gain = current - height(trial)
            rate = gain / max(cost, 0.5)
            if gain > 0 and (best is None or rate > best[0]):
                best = (rate, trial)
        if best is None:
            break
        widths = best[1]
    spare = avail - sum(widths)
    wraps = any(lines(c, w) > 1 for r in body for c, w in zip(r, widths))
    return [w + spare / n for w in widths], wraps


def size_columns(table: str) -> str:
    # Tables of one-line cells are already l columns at their natural width.
    spec, rule, rest = table.partition("\\toprule")
    if "p{" not in spec:
        return table
    header, mid, after = rest.partition("\\midrule")
    # A header wraps before a number does, but never inside its brackets:
    # "[95%" over "CI]" reads as two broken labels.
    header = re.sub(r"\{\[\}[^{}]*?\{\]\}", lambda m: m.group(0).replace(" ", "~"), header)
    # Nor between a number and its unit: "locked by 72" over "h".
    header = re.sub(r"(\d) (h|d)\b", r"\1~\2", header)
    rest = header + mid + after
    _, sep, body = after.partition("\\endlastfoot")
    rows = body.split(" \\\\\n")[:-1]  # the last piece is "\end{longtable}"
    head = cells(header.replace("\\noalign{}", "").rsplit("\\\\", 1)[0])
    widths, wraps = fit_widths(head, [cells(r) for r in rows])
    avail = sum(widths)
    reals = iter(f"{w / avail:.4f}" for w in widths)
    spec = re.sub(r"\\real\{[0-9.]+\}", lambda _: "\\real{" + next(reals) + "}", spec)
    table = spec + rule + rest
    # booktabs draws no rules between rows, so where a body cell still wraps a
    # three-line cell runs into the next row: space those tables' rows apart.
    if wraps:
        head_part, sep, body = table.partition("\\endlastfoot")
        pieces = body.split(" \\\\\n")
        spaced = " \\\\ \\addlinespace[0.6em]\n".join(pieces[:-1])
        table = head_part + sep + spaced + " \\\\\n" + pieces[-1]
    return table


def keep_together(table: str) -> str:
    # longtable breaks a table wherever the page ends, which left the lock-in
    # table's header and one row at the foot of a page. A table short enough
    # to fit on a page ends its rows with \\* (no break here), so it moves
    # whole to the next page instead. Only one-line rows, at most 15: the
    # wrapped Changes table held together was taller than what was left of
    # page 1, and LaTeX left page 2 blank before it.
    # Any longer table still ties its last two rows to its end: the predictions
    # table broke after its final row, and page 16 opened on a repeated header
    # over the closing rule with nothing between them.
    head, sep, body = table.partition("\\endlastfoot")
    pieces = body.split(" \\\\")
    if not sep:
        return table
    rows = len(pieces) - 1
    whole = "\\addlinespace" not in body and rows <= 15
    free = 0 if whole else max(rows - 2, 0)  # row ends a page may break after
    return head + sep + " \\\\".join(pieces[:free + 1]) + "".join(" \\\\*" + q for q in pieces[free + 1:])


def lead_with_table(s: str) -> str:
    # A table that moves to the next page leaves behind what introduces it: the
    # "7.2" heading alone at the foot of page 11, "The same summary by
    # language:" at the foot of page 4. longtable forces that break itself
    # (\vfil\break when its header and first row won't fit), so no penalty
    # can stop it; instead ask, before the heading or a short paragraph leading
    # into the table, for the room the table needs, so both move together. A
    # long paragraph is left alone: moving it whole would empty the page.
    # A heading just above that paragraph comes along too.
    def reserve(m: re.Match) -> str:
        heading, lead, table = m.group(1), m.group(2), m.group(3)
        if not heading and re.match(r"\\(sub)*section\{", lead):
            heading, lead = lead, ""
        if len(lead) > 300:
            return m.group(0)
        held = table.count("\\\\*")  # held rows: all of a short table, else 2
        lines = held + (3 if heading else 0) + (2 + len(lead) // 90 if lead else 0) + 3
        return f"\\reservespace{{{lines}\\baselineskip}}\n" + m.group(0)
    return re.sub(r"(?<=\n\n)(\\(?:sub)*section\{[^\n]*\n\n)?((?:(?!\n\n).)+?)\n\n"
                  r"(\{\\def\\LTcaptype[^\n]*\n\\begingroup\\small\n\\begin\{longtable\}.*?\\end\{longtable\})",
                  reserve, s, flags=re.S)


def main() -> None:
    s = swap_svg(sys.stdin.read())
    # Tables set a step smaller than the text, as is usual in a paper: the
    # 11-column likes-by-age table overran the margin at full size.
    s = re.sub(r"\\begin\{longtable\}.*?\\end\{longtable\}",
               lambda m: "\\begingroup\\small\n" + keep_together(size_columns(m.group(0))) + "\n\\endgroup",
               s, flags=re.S)
    sys.stdout.write(lead_with_table(s))


if __name__ == "__main__":
    main()
