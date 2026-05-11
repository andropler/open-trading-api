from __future__ import annotations

from pathlib import Path

import pytest

from kis_backtest.live.position.tracker import PositionTracker


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "positions.json"


class TestBasicLifecycle:
    def test_open_then_close_full(self, state_path: Path) -> None:
        t = PositionTracker(state_path)
        t.open_position("005930", 10, 70000, "2026-05-05T09:00:00")
        assert t.get_position("005930").qty == 10
        net = t.close_position("005930", 10, 71000, "2026-05-05T10:00:00")
        # gross 10000 - commission 211.5 - tax 1278 ≈ 8510.5
        assert 8500 < net < 8520
        assert t.get_position("005930") is None
        assert t.state.consecutive_losses == 0

    def test_partial_close(self, state_path: Path) -> None:
        t = PositionTracker(state_path)
        t.open_position("005930", 10, 70000, "2026-05-05T09:00:00")
        t.close_position("005930", 4, 71000, "2026-05-05T10:00:00")
        pos = t.get_position("005930")
        assert pos is not None
        assert pos.qty == 6

    def test_close_exceeds_qty(self, state_path: Path) -> None:
        t = PositionTracker(state_path)
        t.open_position("005930", 5, 70000, "2026-05-05T09:00:00")
        with pytest.raises(ValueError, match="exceeds"):
            t.close_position("005930", 10, 71000, "2026-05-05T10:00:00")

    def test_close_unknown_symbol(self, state_path: Path) -> None:
        t = PositionTracker(state_path)
        with pytest.raises(KeyError):
            t.close_position("000660", 1, 100000, "2026-05-05T09:00:00")

    def test_open_invalid_qty(self, state_path: Path) -> None:
        t = PositionTracker(state_path)
        with pytest.raises(ValueError, match="qty"):
            t.open_position("005930", 0, 70000, "2026-05-05T09:00:00")

    def test_open_invalid_price(self, state_path: Path) -> None:
        t = PositionTracker(state_path)
        with pytest.raises(ValueError, match="price"):
            t.open_position("005930", 10, -1, "2026-05-05T09:00:00")


class TestAveragePrice:
    def test_weighted_average_on_add(self, state_path: Path) -> None:
        t = PositionTracker(state_path)
        t.open_position("005930", 10, 70000, "2026-05-05T09:00:00")
        t.open_position("005930", 10, 80000, "2026-05-05T09:30:00")
        pos = t.get_position("005930")
        assert pos is not None
        assert pos.qty == 20
        assert pos.avg_price == 75000.0


class TestPersistence:
    def test_restart_no_position(self, state_path: Path) -> None:
        PositionTracker(state_path)
        t2 = PositionTracker(state_path)
        assert len(t2.state.positions) == 0

    def test_restart_open_position(self, state_path: Path) -> None:
        t1 = PositionTracker(state_path)
        t1.open_position("005930", 10, 70000, "2026-05-05T09:00:00")
        t2 = PositionTracker(state_path)
        pos = t2.get_position("005930")
        assert pos is not None
        assert pos.qty == 10
        assert pos.avg_price == 70000.0

    def test_restart_after_close(self, state_path: Path) -> None:
        t1 = PositionTracker(state_path)
        t1.open_position("005930", 10, 70000, "2026-05-05T09:00:00")
        t1.close_position("005930", 10, 71000, "2026-05-05T10:00:00")
        t2 = PositionTracker(state_path)
        assert t2.get_position("005930") is None
        assert t2.state.realized_pnl_krw > 0


class TestDailyReset:
    def test_same_day_no_reset(self, state_path: Path) -> None:
        t = PositionTracker(state_path)
        t.open_position("005930", 10, 70000, "2026-05-05T09:00:00")
        t.close_position("005930", 10, 71000, "2026-05-05T10:00:00")
        t.state.daily_date = "2026-05-05"
        before = t.state.daily_realized_pnl_krw
        t.daily_reset("2026-05-05")
        assert t.state.daily_realized_pnl_krw == before

    def test_new_day_resets(self, state_path: Path) -> None:
        t = PositionTracker(state_path)
        t.open_position("005930", 10, 70000, "2026-05-05T09:00:00")
        t.close_position("005930", 10, 71000, "2026-05-05T10:00:00")
        t.state.daily_date = "2026-05-05"
        t.daily_reset("2026-05-06")
        assert t.state.daily_realized_pnl_krw == 0.0
        assert t.state.trades_today == 0
        assert t.state.daily_date == "2026-05-06"

    def test_consecutive_losses_survives_daily_reset(self, state_path: Path) -> None:
        # 야간 리셋이 "연속 N회 손절" 킬스위치 조건을 우회하지 않도록
        # consecutive_losses 는 날짜 경계 무관 rolling 카운터여야 한다.
        t = PositionTracker(state_path)
        t.open_position("005930", 10, 70000, "2026-05-05T09:00:00")
        t.close_position("005930", 10, 65000, "2026-05-05T10:00:00")
        assert t.state.consecutive_losses == 1
        t.state.daily_date = "2026-05-05"
        t.daily_reset("2026-05-06")
        assert t.state.consecutive_losses == 1


class TestCorruption:
    def test_corrupt_file_raises_and_backs_up(self, state_path: Path) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{not json", encoding="utf-8")
        with pytest.raises(RuntimeError, match="corrupted"):
            PositionTracker(state_path)
        backups = list(state_path.parent.glob("positions.corrupt-*.json"))
        assert len(backups) == 1


class TestLossTracking:
    def test_consecutive_losses_count(self, state_path: Path) -> None:
        t = PositionTracker(state_path)
        t.open_position("005930", 10, 70000, "2026-05-05T09:00:00")
        t.close_position("005930", 10, 65000, "2026-05-05T10:00:00")
        assert t.state.consecutive_losses == 1
        t.open_position("000660", 5, 100000, "2026-05-05T11:00:00")
        t.close_position("000660", 5, 95000, "2026-05-05T12:00:00")
        assert t.state.consecutive_losses == 2
        t.open_position("373220", 3, 200000, "2026-05-05T13:00:00")
        t.close_position("373220", 3, 220000, "2026-05-05T14:00:00")
        assert t.state.consecutive_losses == 0


class TestExposure:
    def test_total_exposure(self, state_path: Path) -> None:
        t = PositionTracker(state_path)
        t.open_position("005930", 10, 70000, "2026-05-05T09:00:00")
        t.open_position("000660", 5, 100000, "2026-05-05T09:10:00")
        assert t.total_exposure() == 10 * 70000 + 5 * 100000
