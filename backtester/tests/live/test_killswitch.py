from __future__ import annotations

from pathlib import Path

import pytest

from kis_backtest.live.risk.killswitch import (
    Killswitch,
    TradingMetrics,
)


@pytest.fixture
def ks(tmp_path: Path) -> Killswitch:
    return Killswitch(
        halt_flag_path=tmp_path / "HALT.flag",
        archive_dir=tmp_path / "halts",
        capital_krw=5_000_000,
    )


class TestNotTriggered:
    def test_normal_state_no_halt(self, ks: Killswitch) -> None:
        m = TradingMetrics(
            daily_realized_pnl_krw=-50_000,
            cumulative_realized_pnl_krw=-100_000,
            consecutive_losses=2,
            ws_disconnect_seconds=60,
            api_5xx_count_5min=2,
        )
        reason = ks.evaluate(m, "2026-05-05T10:00:00")
        assert reason is None
        assert not ks.is_halted()


class TestDailyLoss:
    def test_daily_loss_3pct_triggers(self, ks: Killswitch) -> None:
        m = TradingMetrics(daily_realized_pnl_krw=-150_001)
        reason = ks.evaluate(m, "2026-05-05T10:00:00")
        assert reason is not None
        assert reason.condition_id == "daily_loss"
        assert ks.is_halted()


class TestCumulativeLoss:
    def test_cumulative_8pct_triggers(self, ks: Killswitch) -> None:
        m = TradingMetrics(cumulative_realized_pnl_krw=-400_001)
        reason = ks.evaluate(m, "2026-05-05T10:00:00")
        assert reason is not None
        assert reason.condition_id == "cumulative_loss"


class TestConsecutiveLosses:
    def test_three_consecutive_triggers(self, ks: Killswitch) -> None:
        m = TradingMetrics(consecutive_losses=3)
        reason = ks.evaluate(m, "2026-05-05T10:00:00")
        assert reason is not None
        assert reason.condition_id == "consecutive_losses"


class TestWsDisconnect:
    def test_5min_disconnect_triggers(self, ks: Killswitch) -> None:
        m = TradingMetrics(ws_disconnect_seconds=300)
        reason = ks.evaluate(m, "2026-05-05T10:00:00")
        assert reason is not None
        assert reason.condition_id == "ws_disconnect"


class TestApi5xx:
    def test_5xx_burst_triggers(self, ks: Killswitch) -> None:
        m = TradingMetrics(api_5xx_count_5min=5)
        reason = ks.evaluate(m, "2026-05-05T10:00:00")
        assert reason is not None
        assert reason.condition_id == "api_5xx"


class TestHaltFlag:
    def test_flag_persists_and_reloadable(self, ks: Killswitch) -> None:
        m = TradingMetrics(daily_realized_pnl_krw=-200_000)
        ks.evaluate(m, "2026-05-05T10:00:00")
        assert ks.is_halted()
        ks2 = Killswitch(
            halt_flag_path=ks.halt_flag_path,
            archive_dir=ks.archive_dir,
            capital_krw=5_000_000,
        )
        assert ks2.is_halted()
        reason = ks2.read_halt_reason()
        assert reason is not None
        assert reason.condition_id == "daily_loss"

    def test_evaluate_after_halt_no_overwrite(self, ks: Killswitch) -> None:
        m1 = TradingMetrics(daily_realized_pnl_krw=-200_000)
        ks.evaluate(m1, "2026-05-05T10:00:00")
        m2 = TradingMetrics(consecutive_losses=10)
        reason = ks.evaluate(m2, "2026-05-05T11:00:00")
        assert reason is None


class TestManualResume:
    def test_resume_archives_flag(self, ks: Killswitch) -> None:
        m = TradingMetrics(daily_realized_pnl_krw=-200_000)
        ks.evaluate(m, "2026-05-05T10:00:00")
        archive = ks.manual_resume()
        assert archive is not None
        assert archive.exists()
        assert not ks.is_halted()

    def test_resume_when_not_halted(self, ks: Killswitch) -> None:
        archive = ks.manual_resume()
        assert archive is None


class TestInvalidConstruction:
    def test_zero_capital_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="capital_krw"):
            Killswitch(
                halt_flag_path=tmp_path / "HALT.flag",
                archive_dir=tmp_path / "halts",
                capital_krw=0,
            )
