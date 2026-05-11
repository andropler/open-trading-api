"""포지션 트래커: JSON 파일에 영속화 + 가중평균 평균단가 + 실현손익 누적.

저장 포맷은 JSON. 모든 쓰기는 tmp 파일 + os.replace 로 atomic.
손상된 JSON은 .corrupt-{ts} 로 백업하고 RuntimeError를 발생.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# 한국 주식 거래 비용 (단순 모델)
COMMISSION_PCT = 0.00015  # 매수+매도 양방향 각 0.015%
TRANSACTION_TAX_PCT = 0.0018  # 매도시 거래세 0.18%


@dataclass
class LivePosition:
    symbol: str
    qty: int
    avg_price: float
    entry_ts: str
    side: str = "long"


@dataclass
class TrackerState:
    positions: dict[str, LivePosition] = field(default_factory=dict)
    realized_pnl_krw: float = 0.0
    daily_realized_pnl_krw: float = 0.0
    daily_date: str = ""
    trades_today: int = 0
    consecutive_losses: int = 0


class PositionTracker:
    def __init__(self, state_path: Path | str):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load()

    def _load(self) -> TrackerState:
        if not self.state_path.exists():
            return TrackerState()
        raw = self.state_path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            backup = self.state_path.with_name(
                f"{self.state_path.stem}.corrupt-{int(time.time())}{self.state_path.suffix}"
            )
            shutil.move(str(self.state_path), str(backup))
            raise RuntimeError(
                f"position state file corrupted at {self.state_path}, backed up to {backup}: {e}"
            ) from e
        positions = {
            sym: LivePosition(**pos) for sym, pos in data.get("positions", {}).items()
        }
        return TrackerState(
            positions=positions,
            realized_pnl_krw=float(data.get("realized_pnl_krw", 0.0)),
            daily_realized_pnl_krw=float(data.get("daily_realized_pnl_krw", 0.0)),
            daily_date=data.get("daily_date", ""),
            trades_today=int(data.get("trades_today", 0)),
            consecutive_losses=int(data.get("consecutive_losses", 0)),
        )

    def _flush(self) -> None:
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(asdict(self.state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(self.state_path))

    def open_position(self, symbol: str, qty: int, price: float, ts: str) -> None:
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        if price <= 0:
            raise ValueError(f"price must be positive, got {price}")
        existing = self.state.positions.get(symbol)
        if existing is None:
            self.state.positions[symbol] = LivePosition(
                symbol=symbol, qty=qty, avg_price=float(price), entry_ts=ts
            )
        else:
            total_qty = existing.qty + qty
            existing.avg_price = (
                existing.avg_price * existing.qty + price * qty
            ) / total_qty
            existing.qty = total_qty
        self._flush()

    def close_position(self, symbol: str, qty: int, price: float, ts: str) -> float:
        if symbol not in self.state.positions:
            raise KeyError(f"no open position for {symbol}")
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        if price <= 0:
            raise ValueError(f"price must be positive, got {price}")
        pos = self.state.positions[symbol]
        if qty > pos.qty:
            raise ValueError(f"close qty {qty} exceeds open qty {pos.qty} for {symbol}")
        gross = (price - pos.avg_price) * qty
        buy_cost = pos.avg_price * qty
        sell_proceeds = price * qty
        commission = (buy_cost + sell_proceeds) * COMMISSION_PCT
        tax = sell_proceeds * TRANSACTION_TAX_PCT
        net = gross - commission - tax
        self.state.realized_pnl_krw += net
        self.state.daily_realized_pnl_krw += net
        self.state.trades_today += 1
        if net < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0
        pos.qty -= qty
        if pos.qty == 0:
            del self.state.positions[symbol]
        self._flush()
        return net

    def daily_reset(self, today: str) -> None:
        """일별 카운터(daily_realized_pnl, trades_today) 초기화.

        consecutive_losses는 날짜 경계 무관 rolling 카운터이므로 초기화하지 않는다.
        킬스위치의 "연속 N회 손절" 조건이 야간 리셋으로 우회되는 것을 방지하기 위함.
        """
        if self.state.daily_date != today:
            self.state.daily_realized_pnl_krw = 0.0
            self.state.trades_today = 0
            self.state.daily_date = today
            self._flush()

    def get_position(self, symbol: str) -> LivePosition | None:
        return self.state.positions.get(symbol)

    def total_exposure(self) -> float:
        return sum(p.qty * p.avg_price for p in self.state.positions.values())
