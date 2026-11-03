from jobradar.sources.karriere import KarriereSource


class S:  # minimal settings stand-in
    def get(self, *a, **k):
        return None


def test_queries_rotate_and_backfill_runs_all():
    src = KarriereSource(S(), {"keywords": ["a", "b", "c", "d", "e", "f"], "keywords_per_run": 2,
                               "keyword_pages": 2, "pages": 4, "interval_sec": 300})
    qs = src._queries(False)
    assert qs[0][1] == 4 and len(qs) == 3 and all(p == 2 for _, p in qs[1:])
    bf = src._queries(True)
    assert len(bf) == 1 + 6


def test_no_keywords_is_generic_only():
    assert len(KarriereSource(S(), {})._queries(False)) == 1
