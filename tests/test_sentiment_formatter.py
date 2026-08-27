"""
제목: SentimentFormatter / SentimentStore / run_sentiment 단위 테스트
내용: 2종 포맷 생성, anti-bot 랜덤 변형, 결측 '—' 표기, HY 기준일 분리,
      DEGRADED 발행 스킵, upsert row 구성 검증.
"""

from __future__ import annotations

import random
from unittest.mock import MagicMock, patch

import pytest

from detection.sentiment_engine import SentimentEngine
from publishers.sentiment_formatter import SentimentFormatter


def _result(
    vix_ratio: float | None = 0.94,
    pcr: float | None = 0.81,
    hy_oas: float | None = 318.0,
    breadth: float | None = -1.4,
    crypto_fg: float | None = 38.0,
    prev_e: float | None = 47.4,
):
    """제목: 테스트용 SentimentResult 생성기"""

    def _item(value: float | None, date: str = "2026-08-27") -> dict | None:
        if value is None:
            return None
        return {"value": value, "date": date}

    collected = {
        "vix_ratio": _item(vix_ratio),
        "pcr": _item(pcr),
        "hy_oas": _item(hy_oas, date="2026-08-26"),  # T+1 지연 재현
        "breadth": _item(breadth),
        "crypto_fg": _item(crypto_fg),
    }
    if collected["vix_ratio"] is not None:
        collected["vix_ratio"].update({"vix": 18.8, "vix3m": 20.0})
    return SentimentEngine().compute(collected, prev_e_score=prev_e)


class TestFormats:
    """제목: 2종 포맷 생성"""

    @pytest.mark.unit
    def test_variant_b_contains_core_fields(self):
        """제목: Variant B — E 점수/레벨/지표/기준일/디스클레이머 포함"""
        fmt = SentimentFormatter(rng=random.Random(1))
        text = fmt.format_variant_b(_result(), "2026-08-27")
        assert "/ 100" in text
        assert "공포" in text
        assert "VIX구조 0.94" in text
        assert "318bp" in text
        assert "08/27" in text
        assert "HY: 08/26" in text  # T+1 기준일 분리 표기
        assert "⚠️" in text
        assert "#" in text

    @pytest.mark.unit
    def test_edt_contains_three_axes(self):
        """제목: EDT — E/D/T 3축 + 상태 라벨 + 브랜드 태그"""
        fmt = SentimentFormatter(rng=random.Random(2))
        result = _result()
        text = fmt.format_edt(result, "2026-08-27")
        assert "E " in text
        assert "D " in text
        assert "T " in text
        assert "#EDT지표" in text
        assert result.state_label is not None
        assert f"상태: {result.state_label}" in text

    @pytest.mark.unit
    def test_edt_cold_start_shows_dash(self):
        """제목: EDT 콜드스타트 — T '—' 표기 (추측값 금지)"""
        fmt = SentimentFormatter(rng=random.Random(3))
        text = fmt.format_edt(_result(prev_e=None), "2026-08-27")
        assert "T —" in text
        assert "이력 축적 중" in text

    @pytest.mark.unit
    def test_missing_indicator_dash(self):
        """제목: 결측 지표 '—' 표기 + 결측 안내"""
        fmt = SentimentFormatter(rng=random.Random(4))
        text = fmt.format_variant_b(_result(pcr=None), "2026-08-27")
        assert "PCR —" in text
        assert "수집 실패" in text

    @pytest.mark.unit
    def test_random_variation_anti_bot(self):
        """제목: anti-bot — 시드가 다르면 동일 데이터라도 텍스트 상이"""
        result = _result()
        texts = {
            SentimentFormatter(rng=random.Random(seed)).format_x_random(
                result, "2026-08-27"
            )
            for seed in range(8)
        }
        assert len(texts) >= 3  # 포맷 2종 × 문구/해시태그 조합

    @pytest.mark.unit
    def test_x_random_length_guard(self):
        """제목: X 발행 텍스트 길이 상한 (프리미엄 25,000자 내 안전 여유)"""
        for seed in range(10):
            fmt = SentimentFormatter(rng=random.Random(seed))
            text = fmt.format_x_random(_result(), "2026-08-27")
            assert len(text) < 1000

    @pytest.mark.unit
    def test_tg_internal_full_detail(self):
        """제목: TG Internal — 원값/점수/결측 전체 노출"""
        fmt = SentimentFormatter(rng=random.Random(5))
        text = fmt.format_tg_internal(_result(hy_oas=None), "2026-08-27")
        assert "[EDT internal]" in text
        assert "MISSING" in text
        assert "vix_ratio" in text


class TestSentimentStore:
    """제목: SentimentStore (Supabase mock)"""

    @pytest.mark.unit
    def test_upsert_requires_score_date(self):
        """제목: score_date 없는 row 거부"""
        from db.sentiment_store import SentimentStore

        store = SentimentStore(supabase_url="https://x.supabase.co", supabase_key="k")
        assert store.upsert_daily({"e_score": 50.0}) is False

    @pytest.mark.unit
    def test_upsert_calls_on_conflict(self):
        """제목: upsert가 score_date on_conflict로 호출 (멱등성)"""
        from db.sentiment_store import SentimentStore

        store = SentimentStore(supabase_url="https://x.supabase.co", supabase_key="k")
        client = MagicMock()
        store._client = client
        ok = store.upsert_daily({"score_date": "2026-08-27", "e_score": 44.9})
        assert ok is True
        client.table.assert_called_with("ia_sentiment_daily")
        _, kwargs = client.table.return_value.upsert.call_args
        assert kwargs.get("on_conflict") == "score_date"

    @pytest.mark.unit
    def test_fetch_e_score_days_ago_cold_start(self):
        """제목: 이력 부족(콜드스타트) 시 None"""
        from db.sentiment_store import SentimentStore

        store = SentimentStore(supabase_url="https://x.supabase.co", supabase_key="k")
        client = MagicMock()
        query = client.table.return_value.select.return_value
        query = query.lt.return_value.order.return_value.limit.return_value
        query.execute.return_value = MagicMock(data=[{"score_date": "2026-08-26",
                                                      "e_score": 47.4}])
        store._client = client
        assert store.fetch_e_score_days_ago(5, today="2026-08-27") is None

    @pytest.mark.unit
    def test_fetch_e_score_days_ago_hit(self):
        """제목: 5영업일 룩백 — 5번째 row의 e_score 반환"""
        from db.sentiment_store import SentimentStore

        store = SentimentStore(supabase_url="https://x.supabase.co", supabase_key="k")
        client = MagicMock()
        rows = [{"score_date": f"2026-08-{26 - i}", "e_score": 40.0 + i} for i in range(5)]
        query = client.table.return_value.select.return_value
        query = query.lt.return_value.order.return_value.limit.return_value
        query.execute.return_value = MagicMock(data=rows)
        store._client = client
        assert store.fetch_e_score_days_ago(5, today="2026-08-27") == pytest.approx(44.0)


class TestRunSentiment:
    """제목: run_sentiment 파이프라인 (전체 mock)"""

    @pytest.mark.unit
    def test_degraded_skips_publish(self):
        """제목: DEGRADED 시 발행 스킵 + upsert는 수행 (default-deny)"""
        import run_sentiment

        collected_fail = {
            "vix_ratio": None,
            "pcr": None,
            "hy_oas": {"value": 318.0, "date": "2026-08-26"},
            "breadth": {"value": -1.4, "date": "2026-08-27"},
            "crypto_fg": {"value": 38.0, "date": "2026-08-27"},
        }
        store = MagicMock()
        store.fetch_e_score_days_ago.return_value = None
        store.upsert_daily.return_value = True
        x_pub = MagicMock()
        tg_pub = MagicMock()

        with (
            patch("run_sentiment.get_market_profile", return_value="extended"),
            patch("run_sentiment.SentimentCollector") as coll_cls,
            patch("run_sentiment.SentimentStore", return_value=store),
            patch("run_sentiment.XPublisher", return_value=x_pub),
            patch("run_sentiment.TelegramPublisher", return_value=tg_pub),
            pytest.raises(SystemExit) as exc,
        ):
            coll_cls.return_value.collect_all.return_value = collected_fail
            run_sentiment.main()

        assert exc.value.code == 0
        store.upsert_daily.assert_called_once()  # 기록은 남긴다
        x_pub.publish.assert_not_called()  # 발행은 스킵
        tg_pub.publish_internal.assert_not_called()

    @pytest.mark.unit
    def test_holiday_skips_pipeline(self):
        """제목: 휴장일 조기 종료 — 수집조차 하지 않음"""
        import run_sentiment
        from config.market_calendar import PROFILE_HOLIDAY

        with (
            patch("run_sentiment.get_market_profile", return_value=PROFILE_HOLIDAY),
            patch("run_sentiment.SentimentCollector") as coll_cls,
            pytest.raises(SystemExit) as exc,
        ):
            run_sentiment.main()
        assert exc.value.code == 0
        coll_cls.return_value.collect_all.assert_not_called()

    @pytest.mark.unit
    def test_normal_path_publishes_and_marks(self):
        """제목: 정상 경로 — X+TG 발행, DRY_RUN=false면 플래그 기록"""
        import run_sentiment

        collected = {
            "vix_ratio": {"value": 0.94, "date": "2026-08-27", "vix": 18.8,
                          "vix3m": 20.0},
            "pcr": {"value": 0.81, "date": "2026-08-27"},
            "hy_oas": {"value": 318.0, "date": "2026-08-26"},
            "breadth": {"value": -1.4, "date": "2026-08-27"},
            "crypto_fg": {"value": 38.0, "date": "2026-08-27"},
        }
        store = MagicMock()
        store.fetch_e_score_days_ago.return_value = 47.4
        store.upsert_daily.return_value = True
        x_pub = MagicMock()
        x_pub.publish.return_value = "tweet_id_123"
        tg_pub = MagicMock()

        with (
            patch("run_sentiment.get_market_profile", return_value="extended"),
            patch("run_sentiment.DRY_RUN", False),
            patch("run_sentiment.SentimentCollector") as coll_cls,
            patch("run_sentiment.SentimentStore", return_value=store),
            patch("run_sentiment.XPublisher", return_value=x_pub),
            patch("run_sentiment.TelegramPublisher", return_value=tg_pub),
            patch("run_sentiment._publish_jitter"),
        ):
            coll_cls.return_value.collect_all.return_value = collected
            run_sentiment.main()

        x_pub.publish.assert_called_once()
        tg_pub.publish_internal.assert_called_once()
        marked = {c.args for c in store.mark_published.call_args_list}
        assert ("2026-08-27", "x") in marked
        assert ("2026-08-27", "tg_internal") in marked

    @pytest.mark.unit
    def test_x_failure_isolated_no_retry(self):
        """제목: X 발행 실패 격리 — 무재시도, TG는 계속 진행"""
        import run_sentiment

        collected = {
            "vix_ratio": {"value": 0.94, "date": "2026-08-27", "vix": 18.8,
                          "vix3m": 20.0},
            "pcr": {"value": 0.81, "date": "2026-08-27"},
            "hy_oas": {"value": 318.0, "date": "2026-08-26"},
            "breadth": {"value": -1.4, "date": "2026-08-27"},
            "crypto_fg": {"value": 38.0, "date": "2026-08-27"},
        }
        store = MagicMock()
        store.fetch_e_score_days_ago.return_value = None
        x_pub = MagicMock()
        x_pub.publish.side_effect = RuntimeError("X down")
        tg_pub = MagicMock()

        with (
            patch("run_sentiment.get_market_profile", return_value="extended"),
            patch("run_sentiment.SentimentCollector") as coll_cls,
            patch("run_sentiment.SentimentStore", return_value=store),
            patch("run_sentiment.XPublisher", return_value=x_pub),
            patch("run_sentiment.TelegramPublisher", return_value=tg_pub),
            patch("run_sentiment._publish_jitter"),
        ):
            coll_cls.return_value.collect_all.return_value = collected
            run_sentiment.main()

        assert x_pub.publish.call_count == 1  # 무재시도
        tg_pub.publish_internal.assert_called_once()  # 격리 후 계속
