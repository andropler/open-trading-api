"""LiveTrader factory — .env.live 한 파일로 모든 컴포넌트를 wire-up.

KIS 자격증명은 .env.live → kis_devlp.yaml 자동 동기화 후 ka.auth() 호출로
토큰 발급. KIS 의존성 import 는 yaml 동기화 직후에 수행해야 안전.

상태 파일은 state_dir 아래 보존 (positions.json, HALT.flag, daily/, snap/).
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path
from typing import Iterable, Optional

from kis_backtest.live.config.credentials import LiveConfig
from kis_backtest.live.config.kis_yaml_sync import sync_kis_yaml
from kis_backtest.live.data.bar_aggregator import FiveMinuteBarAggregator
from kis_backtest.live.data.bar_buffer import FiveMinuteBarBuffer
from kis_backtest.live.data.cache import DailyOHLCVCache
from kis_backtest.live.data.kis_fetcher import KISDailyFetcher
from kis_backtest.live.notify.telegram import HttpxTransport, TelegramClient
from kis_backtest.live.orchestrator.kis_executor import KISExecutorAdapter
from kis_backtest.live.orchestrator.live_trader import LiveTrader
from kis_backtest.live.orchestrator.monitors import Api5xxMonitor, WsHealthMonitor
from kis_backtest.live.position.tracker import PositionTracker
from kis_backtest.live.risk.killswitch import Killswitch, KillswitchLimits
from kis_backtest.live.signal.engine import SignalEngine


def build_live_trader(
    env_path: Path,
    *,
    today: _date,
    engines: Iterable[SignalEngine] = (),
    state_dir: Optional[Path] = None,
    market_symbol: str = "069500",
    enable_telegram: bool = True,
) -> LiveTrader:
    """매일 운영 시 진입점. .env.live 한 줄로 전체 컴포넌트 빌드.

    KIS yaml 동기화는 부수효과 — backup 파일이 ~/KIS/config/ 에 남는다.
    """
    config = LiveConfig.from_env(env_path)
    sync_kis_yaml(config.kis)

    # kis_auth 는 module-level 에서 yaml 읽으므로 sync 이후 import
    from kis_backtest.providers.kis.auth import KISAuth
    from kis_backtest.providers.kis.brokerage import KISBrokerageProvider
    from kis_backtest.providers.kis.data import KISDataProvider

    auth = KISAuth(
        app_key=config.kis.appkey,
        app_secret=config.kis.appsecret,
        account_no=config.kis.account_no,
        is_paper=(config.mode == "vps"),
    )
    fetcher = KISDailyFetcher(KISDataProvider(auth))
    # KISBrokerageProvider 는 from_auth 클래스메서드로 KISAuth 인스턴스 공유
    # (직접 __init__ 은 app_key/secret/account_no 를 다시 받아 ka.auth() 중복 호출)
    executor = KISExecutorAdapter(KISBrokerageProvider.from_auth(auth))

    state = Path(state_dir) if state_dir else (Path.home() / "KIS" / "live_state")
    state.mkdir(parents=True, exist_ok=True)

    cache = DailyOHLCVCache(state / "daily")
    buffer = FiveMinuteBarBuffer(snapshot_dir=state / "snap")
    aggregator = FiveMinuteBarAggregator(buffer=buffer, today=today)
    tracker = PositionTracker(state / "positions.json")
    killswitch = Killswitch(
        halt_flag_path=state / "HALT.flag",
        archive_dir=state / "halts",
        capital_krw=config.limits.capital_krw,
        limits=KillswitchLimits(
            daily_loss_pct=config.limits.daily_loss_pct,
            cumulative_loss_pct=config.limits.cumulative_loss_pct,
        ),
    )

    telegram: TelegramClient | None = None
    if enable_telegram:
        telegram = TelegramClient(
            creds=config.telegram, transport=HttpxTransport(timeout=10.0)
        )

    return LiveTrader(
        config=config,
        fetcher=fetcher,
        cache=cache,
        bar_buffer=buffer,
        aggregator=aggregator,
        executor=executor,
        tracker=tracker,
        killswitch=killswitch,
        ws_monitor=WsHealthMonitor(),
        api_monitor=Api5xxMonitor(),
        engines=list(engines),
        telegram=telegram,
        market_symbol=market_symbol,
    )
