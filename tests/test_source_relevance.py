from crime_pipeline.source_relevance import (
    best_victim_name,
    is_weak_body_only_virtual,
    refine_source_groups,
    records_same_case,
    victim_names_compatible,
)


def test_best_victim_name_prefers_script_specific_names() -> None:
    assert best_victim_name({
        "victim_name": None,
        "victim_name_ar": "محمد حسين الترابين",
        "victim_name_he": "מוחמד חוסיין תראבין",
    }) == "محمد حسين الترابين"


def test_victim_names_compatible_keeps_fuller_bakr_name() -> None:
    ok, reason = victim_names_compatible("بكر ياسين", "بكر محمود ياسين")
    assert ok, reason


def test_victim_names_compatible_rejects_father_son_subset() -> None:
    ok, _reason = victim_names_compatible("نظيم نصار", "أدهم نظيم نصار")
    assert not ok


def test_records_same_case_rejects_competing_named_victim() -> None:
    ok, reason = records_same_case(
        {
            "victim_name": "محمد حسين الترابين",
            "city": "ترابين الصانع",
            "incident_date": "2026-01-04",
        },
        {
            "victim_name": "شريف حديد",
            "city": "دالية الكرمل",
            "incident_date": "2026-01-09",
        },
    )
    assert not ok
    assert reason.startswith("name_mismatch")


def test_refine_source_groups_splits_tarabin_from_shareef_hadid() -> None:
    records = {
        "tarabin": {
            "victim_name": "محمد حسين الترابين",
            "city": "ترابين الصانع",
            "incident_date": "2026-01-04",
            "title": "ترابين الصانع: مقتل محمد حسين الترابين برصاص الشرطة",
        },
        "shareef_1": {
            "victim_name": "شريف حديد",
            "city": "دالية الكرمل",
            "incident_date": "2026-01-09",
            "title": "مقتل الشاب شريف حديد من دالية الكرمل",
        },
        "shareef_2": {
            "victim_name": "الشاب شريف حديد",
            "city": "دالية الكرمل",
            "incident_date": "2026-01-14",
            "title": "تمديد اعتقال مشتبهين بقتل الشاب شريف حديد",
        },
    }

    groups = refine_source_groups(
        [["tarabin", "shareef_1", "shareef_2"]],
        records,
    )

    normalized = {frozenset(group) for group in groups}
    assert normalized == {
        frozenset({"tarabin"}),
        frozenset({"shareef_1", "shareef_2"}),
    }


def test_body_only_additional_victim_from_roundup_is_dropped() -> None:
    primary = {
        "victim_index": 0,
        "victim_name_ar": "شريف حديد",
        "city": "دالية الكرمل",
        "incident_date": "2026-01-08",
    }
    additional = {
        "victim_index": 1,
        "victim_name_ar": "محمد حسين الترابين",
        "city": "ترابين الصانع",
        "incident_date": None,
    }

    drop, reason = is_weak_body_only_virtual(
        additional,
        primary,
        article_title="مقتل شاب من دالية الكرمل برصاص جندي على شارع 6 شرق حيفا",
        article_url="https://www.arab48.com/الأخبار/2026/01/08/مقتل-شاب-من-دالية",
    )

    assert drop
    assert reason == "body_only_additional_victim"


def test_additional_victim_named_in_page_identity_is_kept() -> None:
    primary = {
        "victim_index": 0,
        "victim_name_ar": "كامل حجيرات",
        "city": "شفاعمرو",
        "incident_date": "2026-01-07",
    }
    additional = {
        "victim_index": 1,
        "victim_name_ar": "محمود جاسر أبو عرار",
        "city": "عرعرة النقب",
        "incident_date": "2026-01-07",
    }

    drop, reason = is_weak_body_only_virtual(
        additional,
        primary,
        article_title="مقتل 3 أشخاص من شفاعمرو وطالب طب محمود أبو عرار",
        article_url="https://www.arab48.com/محليات/2026/01/07/محمود-أبو-عرار",
    )

    assert not drop
    assert reason == "page_identity_mentions_victim"


def test_same_incident_additional_victim_is_kept_without_name_in_title() -> None:
    primary = {
        "victim_index": 0,
        "victim_name_ar": "ياسر حجيرات",
        "city": "شفاعمرو",
        "incident_date": "2026-01-07",
    }
    additional = {
        "victim_index": 1,
        "victim_name_ar": "كامل حجيرات",
        "city": "شفاعمرو",
        "incident_date": "2026-01-07",
    }

    drop, reason = is_weak_body_only_virtual(
        additional,
        primary,
        article_title="مقتل 3 أشخاص في جريمة إطلاق نار قرب شفاعمرو",
        article_url="https://www.arab48.com/محليات/2026/01/07/مقتل-3-اشخاص",
    )

    assert not drop
    assert reason == "shares_primary_incident_anchor"
