"""CSV row parser — deliberately buggy for T02 eval."""


def parse_csv_row(line: str) -> list[str]:
    """Parse a single CSV row into a list of string values.
    
    BUG: Values are not stripped of trailing whitespace.
    """
    return line.split(",")
