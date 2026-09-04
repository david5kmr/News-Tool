from mi.dedupe import (
    canonical_url,
    find_duplicate,
    normalize_title,
    title_similarity,
    url_hash,
)


class TestCanonicalUrl:
    def test_strips_tracking_and_fragment(self):
        assert canonical_url(
            "https://www.example.de/a/?utm_source=nl&id=7&fbclid=x#top"
        ) == "https://example.de/a?id=7"

    def test_www_and_scheme_variants_collapse(self):
        assert url_hash("https://www.aerzteblatt.de/n/1") == url_hash(
            "https://aerzteblatt.de/n/1/"
        )

    def test_keeps_meaningful_query(self):
        assert "id=42" in canonical_url("https://example.de/artikel?id=42")

    def test_different_articles_stay_different(self):
        assert url_hash("https://x.de/a/1") != url_hash("https://x.de/a/2")


class TestTitleSimilarity:
    def test_reordered_title_is_a_duplicate(self):
        a = normalize_title("Kreiskrankenhaus Ehingen meldet Insolvenz an")
        b = normalize_title("Insolvenz: Das Kreiskrankenhaus Ehingen meldet an")
        assert title_similarity(a, b) >= 0.86

    def test_unrelated_titles_are_not(self):
        a = normalize_title("Kreiskrankenhaus Ehingen meldet Insolvenz an")
        b = normalize_title("GOAEneu: BAEK und PKV einigen sich auf Zeitplan")
        assert title_similarity(a, b) < 0.5

    def test_stopwords_removed(self):
        assert "der" not in normalize_title("Der Bundesrat und die Laender").split()

    def test_umlauts_survive_normalisation(self):
        assert "bundesärztekammer" in normalize_title("Bundesärztekammer tagt heute")

    def test_find_duplicate_returns_matching_id(self):
        known = [(1, normalize_title("Klinikgruppe meldet Insolvenz an"))]
        probe = normalize_title("Insolvenz: Klinikgruppe meldet an")
        assert find_duplicate(probe, known) == 1

    def test_find_duplicate_returns_none_without_match(self):
        known = [(1, normalize_title("Klinikgruppe meldet Insolvenz an"))]
        assert find_duplicate(normalize_title("BMG legt Entwurf vor"), known) is None

    def test_empty_title_never_matches(self):
        assert find_duplicate("", [(1, "irgendwas")]) is None
