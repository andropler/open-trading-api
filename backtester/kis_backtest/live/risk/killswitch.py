"""보수적 킬스위치: 5가지 한도 위반 감지 + HALT.flag 영속화 + 수동 복구.

발동 조건 (OR):
1. 일일 실현 P&L 자본 대비 -3% 이하
2. 누적 실현 P&L 자본 대비 -8% 이하
3. 연속 손절 ≥ 3회
4. 웹소켓 단절 누적 ≥ 300초
5. 5xx 응답 5분 내 ≥ 5회

발동 시 HALT.flag 파일이 atomic write 되며, manual_resume() 호출까지 신규 진입 차단.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class KillswitchLimits:
    daily_loss_pct: float = 3.0
    cumulative_loss_pct: float = 8.0
    max_consecutive_losses: int = 3
    max_ws_disconnect_seconds: int = 300
    max_5xx_count_5min: int = 5


@dataclass
class TradingMetrics:
    daily_realized_pnl_krw: float = 0.0
    cumulative_realized_pnl_krw: float = 0.0
    consecutive_losses: int = 0
    ws_disconnect_seconds: int = 0
    api_5xx_count_5min: int = 0


@dataclass
class HaltReason:
    condition_id: str
    value: float
    threshold: float
    ts: str


class Killswitch:
    def __init__(
        self,
        halt_flag_path: Path | str,
        archive_dir: Path | str,
        capital_krw: float,
        limits: KillswitchLimits | None = None,
    ):
        if capital_krw <= 0:
            raise ValueError(f"capital_krw must be positive, got {capital_krw}")
        self.halt_flag_path = Path(halt_flag_path)
        self.archive_dir = Path(archive_dir)
        self.halt_flag_path.parent.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.capital_krw = float(capital_krw)
        self.limits = limits or KillswitchLimits()

    def is_halted(self) -> bool:
        return self.halt_flag_path.exists()

    def evaluate(self, metrics: TradingMetrics, now_iso: str) -> HaltReason | None:
        if self.is_halted():
            return None

        daily_pct = (metrics.daily_realized_pnl_krw / self.capital_krw) * 100.0
        if daily_pct <= -self.limits.daily_loss_pct:
            return self._trigger("daily_loss", daily_pct, -self.limits.daily_loss_pct, now_iso)

        cum_pct = (metrics.cumulative_realized_pnl_krw / self.capital_krw) * 100.0
        if cum_pct <= -self.limits.cumulative_loss_pct:
            return self._trigger(
                "cumulative_loss", cum_pct, -self.limits.cumulative_loss_pct, now_iso
            )

        if metrics.consecutive_losses >= self.limits.max_consecutive_losses:
            return self._trigger(
                "consecutive_losses",
                float(metrics.consecutive_losses),
                float(self.limits.max_consecutive_losses),
                now_iso,
            )

        if metrics.ws_disconnect_seconds >= self.limits.max_ws_disconnect_seconds:
            return self._trigger(
                "ws_disconnect",
                float(metrics.ws_disconnect_seconds),
                float(self.limits.max_ws_disconnect_seconds),
                now_iso,
            )

        if metrics.api_5xx_count_5min >= self.limits.max_5xx_count_5min:
            return self._trigger(
                "api_5xx",
                float(metrics.api_5xx_count_5min),
                float(self.limits.max_5xx_count_5min),
                now_iso,
            )

        return None

    def _trigger(
        self, condition_id: str, value: float, threshold: float, ts: str
    ) -> HaltReason:
        reason = HaltReason(
            condition_id=condition_id, value=value, threshold=threshold, ts=ts
        )
        tmp = self.halt_flag_path.with_suffix(self.halt_flag_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(asdict(reason), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(str(tmp), str(self.halt_flag_path))
        return reason

    def manual_resume(self) -> Path | None:
        if not self.is_halted():
            return None
        archive = self.archive_dir / f"halt-{int(time.time() * 1000)}.json"
        shutil.move(str(self.halt_flag_path), str(archive))
        return archive

    def read_halt_reason(self) -> HaltReason | None:
        if not self.is_halted():
            return None
        data = json.loads(self.halt_flag_path.read_text(encoding="utf-8"))
        return HaltReason(**data)
