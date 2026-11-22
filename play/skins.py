"""PLAY skins: the same five as Cratedig. Each sets the accent and the dark surfaces; suite_style applies them."""
import suite_style

SKINS = {
    "Blue": dict(suite_style.BLUE),          # the suite default, from the shared file
    "Ember": dict(accent="#ff8a1f", light="#ffc34d", lighter="#fff3c4", bg="#141518", base="#16171a",
                  alt="#1c1d21", mid="#24262b", bar="#1b1c20", line="#2e3036", sel="#4a3424"),
    "Neon": dict(accent="#2bff88", light="#9dffc9", lighter="#e6fff1", bg="#0f1412", base="#111613",
                 alt="#161d19", mid="#1c2420", bar="#141b17", line="#26302b", sel="#1f4632"),
    "Violet": dict(accent="#a46bff", light="#cfa8ff", lighter="#f1e6ff", bg="#131119", base="#15131c",
                   alt="#1b1824", mid="#221f2c", bar="#19161f", line="#2d2939", sel="#3b2c5c"),
    "Crimson": dict(accent="#ff3b5c", light="#ff8fa3", lighter="#ffe0e6", bg="#161113", base="#181315",
                    alt="#1f181b", mid="#261e21", bar="#1c1518", line="#33282c", sel="#55242f"),
}
DEFAULT = "Blue"


def get(name):
    t = dict(SKINS.get(name) or SKINS[DEFAULT])
    t["name"] = name if name in SKINS else DEFAULT
    return t
