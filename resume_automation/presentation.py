import re


MONTHS = "January February March April May June July August September October November December".split()
MONTH_NUMBER = r"(?:0?[1-9]|1[0-2])"
YEAR = r"(?:19|20)\d{2}"
MONTH_WORD = "|".join(m[:3] + "(?:" + m[3:] + ")?" for m in MONTHS) + "|Sept"
DATE = rf"(?:{MONTH_NUMBER}[/.-]{YEAR}|{YEAR}[-/]{MONTH_NUMBER}|(?:{MONTH_WORD})\.?[\s,'/-]+{YEAR}|{YEAR})"


def display_duration(value):
    match = re.fullmatch(rf"({DATE})(?:\s*(?:[-–—]|\bto\b)\s*({DATE}|present|current|ongoing|till date))?",
                         value.strip(), re.I)
    if not match:
        return value

    def endpoint(token):
        year = re.search(YEAR, token)
        if not year:
            return "Present"
        month = (token[:year.start()] + token[year.end():]).strip(" ./,-'")
        if not month:
            return year.group()
        name = MONTHS[int(month) - 1] if month.isdigit() else next(
            m for m in MONTHS if m.lower().startswith(month[:3].lower()))
        return f"{name} {year.group()}"

    return " – ".join(endpoint(part) for part in match.groups() if part)


def private_names(resume):
    names = set(resume.private_names)
    for project in resume.projects:
        names.add(project.organization_name)
        # Older payloads can contain an employer appended to the role.
        suffix = re.search(r"(?:,\s*|\s+at\s+|\s+@\s+)([^,]*(?:technolog(?:y|ies)|solutions|"
                           r"limited|ltd\.?|inc\.?|corp\.?|llc))\s*$", project.role, re.I)
        if suffix:
            names.add(suffix[1])
    names = sorted({name.strip() for name in names if name.strip()}, key=len, reverse=True)
    if names:
        return re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, names)) + r")(?!\w)", re.I)


def display_text(value, names, *, role=False):
    if not names or not names.search(value):
        return value.strip()
    if names.fullmatch(value.strip()):
        return ""
    if role:
        value = re.sub(r"\(\s*" + names.pattern + r"\s*\)", "", value, flags=re.I)
        value = re.sub(r"(?:\s+(?:at|for|in)\s+|\s*[,|@–—-]\s*)" + names.pattern,
                       "", value, flags=re.I)
    value = names.sub("" if role else "the organization", value)
    return " ".join(value.split()).strip(" ,|@–—-")
