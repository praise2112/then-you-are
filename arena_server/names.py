"""The blocklist for stage names and public names."""

from better_profanity import profanity

RESERVED = {
    "then you are",
    "thenyouare",
    "oddstage",
    "the house",
    "house",
    "the judge",
    "judge",
    "admin",
    "moderator",
}
REFUSAL = "That name will not do on a public stage. Pick another."

profanity.load_censor_words()


def check_name(name: str) -> str | None:
    """Returns the refusal to show the player, or None when the name may stand."""
    if name.strip().lower() in RESERVED or profanity.contains_profanity(name):
        return REFUSAL
    return None
