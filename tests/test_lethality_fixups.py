from crime_pipeline.extraction.validator import apply_lethality_fixups


def test_attempted_murder_with_injury_is_marked_survived() -> None:
    extracted = {"victim_outcome": None}
    text = (
        "שני תושבי עראבה נעצרו בחשד לניסיון רצח של ראש העירייה, אחמד נסאר. "
        "נסאר נפצע באורח קשה ופונה לבית החולים."
    )

    fixed = apply_lethality_fixups(extracted, text)

    assert fixed["victim_outcome"] == "survived"


def test_wounded_without_attempted_murder_phrase_is_marked_survived() -> None:
    extracted = {"victim_outcome": None}
    text = (
        "ראש העיר עראבה, ד\"ר אחמד נאסר, נורה הערב ונפצע באורח בינוני. "
        "השניים הועברו לבית החולים פוריה בטבריה לקבלת טיפול רפואי."
    )

    fixed = apply_lethality_fixups(extracted, text)

    assert fixed["victim_outcome"] == "survived"


def test_wounded_article_with_background_death_stats_is_marked_survived() -> None:
    extracted = {"victim_outcome": None}
    text = (
        "ראש העיר עראבה, ד\"ר אחמד נאסר, נפצע באורח בינוני ופונה לבית החולים. "
        "בכתבה צוין כרקע כי בחודש ינואר 2026 לבדו נרצחו 27 בני אדם."
    )

    fixed = apply_lethality_fixups(extracted, text)

    assert fixed["victim_outcome"] == "survived"


def test_homicide_article_with_separate_wounded_person_stays_unknown() -> None:
    extracted = {"victim_outcome": None}
    text = (
        "פלוני נרצח באירוע ירי בכפר. "
        "אדם נוסף נפצע באורח בינוני ופונה לבית החולים."
    )

    fixed = apply_lethality_fixups(extracted, text)

    assert fixed["victim_outcome"] is None


def test_attempted_murder_does_not_override_confirmed_death() -> None:
    extracted = {"victim_outcome": "died"}
    text = (
        "החשוד הואשם בניסיון רצח, אך בהמשך נקבע מותו של הקורבן בבית החולים."
    )

    fixed = apply_lethality_fixups(extracted, text)

    assert fixed["victim_outcome"] == "died"


def test_attempted_murder_with_death_marker_stays_unknown() -> None:
    extracted = {"victim_outcome": None}
    text = (
        "החשוד נעצר בחשד לניסיון רצח. בהמשך נקבע מותו של הקורבן."
    )

    fixed = apply_lethality_fixups(extracted, text)

    assert fixed["victim_outcome"] is None
