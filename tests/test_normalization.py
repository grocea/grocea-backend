from grocea.normalization import clean_name, normalize_name


def test_name_normalization_trims_and_unicode_casefolds_without_collapsing_spaces() -> None:
    assert clean_name("  Brown  Rice  ") == "Brown  Rice"
    assert normalize_name("  STRAẞE  ") == "strasse"
    assert normalize_name("Brown  Rice") != normalize_name("Brown Rice")
