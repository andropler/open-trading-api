from __future__ import annotations

import pytest

from kis_backtest.live.orchestrator.monitors import Api5xxMonitor, WsHealthMonitor


class TestWsHealth:
    def test_normal_connected_zero(self):
        m = WsHealthMonitor()
        assert m.disconnect_seconds(now=1000.0) == 0

    def test_disconnect_then_reconnect(self):
        m = WsHealthMonitor()
        m.on_disconnect(ts=1000.0)
        m.on_reconnect(ts=1030.0)
        assert m.disconnect_seconds(now=1100.0) == 30

    def test_disconnect_in_progress_includes_elapsed(self):
        m = WsHealthMonitor()
        m.on_disconnect(ts=1000.0)
        # 아직 재연결 안 됨 — now 시점까지 진행 시간 포함
        assert m.disconnect_seconds(now=1045.0) == 45

    def test_multiple_cycles_accumulate(self):
        m = WsHealthMonitor()
        m.on_disconnect(ts=1000.0)
        m.on_reconnect(ts=1010.0)
        m.on_disconnect(ts=1100.0)
        m.on_reconnect(ts=1115.0)
        assert m.disconnect_seconds(now=1200.0) == 25  # 10 + 15

    def test_redundant_disconnect_ignored(self):
        m = WsHealthMonitor()
        m.on_disconnect(ts=1000.0)
        m.on_disconnect(ts=1010.0)  # 무시 — 이미 단절 상태
        m.on_reconnect(ts=1030.0)
        assert m.disconnect_seconds(now=1100.0) == 30  # 1000~1030

    def test_reconnect_without_disconnect_noop(self):
        m = WsHealthMonitor()
        m.on_reconnect(ts=1000.0)
        assert m.disconnect_seconds(now=2000.0) == 0


class TestApi5xx:
    def test_empty(self):
        m = Api5xxMonitor()
        assert m.count_5min(now=1000.0) == 0

    def test_within_window(self):
        m = Api5xxMonitor(window_seconds=300)
        m.record_5xx(ts=1000.0)
        m.record_5xx(ts=1100.0)
        m.record_5xx(ts=1200.0)
        assert m.count_5min(now=1250.0) == 3

    def test_eviction_outside_window(self):
        m = Api5xxMonitor(window_seconds=300)
        m.record_5xx(ts=1000.0)
        m.record_5xx(ts=1500.0)  # 500초 후
        # 1500 시점 record 시점에 1000은 자동 evict (1500-300=1200 cutoff)
        assert m.count_5min(now=1500.0) == 1

    def test_count_evicts_at_query(self):
        m = Api5xxMonitor(window_seconds=300)
        m.record_5xx(ts=1000.0)
        # query 시점에 윈도우 외 → evict
        assert m.count_5min(now=2000.0) == 0

    def test_invalid_window(self):
        with pytest.raises(ValueError, match="positive"):
            Api5xxMonitor(window_seconds=0)
