"""LiveTrader factory — .env.live 한 파일로 모든 컴포넌트를 wire-up.

KIS 자격증명은 .env.live → kis_devlp.yaml 자동 동기화 후 ka.auth() 호출로
토큰 발급. KIS 의존성 import 는 yaml 동기화 직후에 수행해야 안전.

상태 파일은 state_dir 아래 보존 (positions.json, HALT.flag, daily/, snap/).

build_live_trader: 트레이더만 빌드 (morning_routine 전용).
build_full_session: 트레이더 + WS launcher + fill subscriber (장중 main loop 용).
"""

from __future__ import annotations

from dataclasses import dataclass
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
from kis_backtest.live.orchestrator.fill_subscriber import KISFillSubscriber
from kis_backtest.live.orchestrator.kis_executor import KISExecutorAdapter
from kis_backtest.live.orchestrator.live_trader import LiveTrader
from kis_backtest.live.orchestrator.monitors import Api5xxMonitor, WsHealthMonitor
from kis_backtest.live.orchestrator.ws_thread import WsThreadLauncher
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


@dataclass
class LiveSession:
    """장중 main loop 에 필요한 모든 컴포넌트 묶음."""

    trader: LiveTrader
    ws_launcher: WsThreadLauncher
    fill_subscriber: KISFillSubscriber


def build_full_session(
    env_path: Path,
    *,
    today: _date,
    engines: Iterable[SignalEngine] = (),
    state_dir: Optional[Path] = None,
    market_symbol: str = "069500",
    enable_telegram: bool = True,
    hts_id: Optional[str] = None,
) -> LiveSession:
    """LiveTrader + KIS WebSocket(별도 thread) + FillSubscriber 묶음.

    실제 KIS WS 인스턴스를 생성하므로 ka.auth_ws() 가 호출됨. 호출자는 반환된
    session.ws_launcher.subscribe_price(symbols, session.trader.on_price) +
    session.fill_subscriber.start() 후 session.ws_launcher.start() 로 thread 가동.
    """
    config = LiveConfig.from_env(env_path)
    sync_kis_yaml(config.kis)

    from kis_backtest.providers.kis.auth import KISAuth
    from kis_backtest.providers.kis.brokerage import KISBrokerageProvider
    from kis_backtest.providers.kis.data import KISDataProvider
    from kis_backtest.providers.kis.websocket import KISWebSocket

    auth = KISAuth(
        app_key=config.kis.appkey,
        app_secret=config.kis.appsecret,
        account_no=config.kis.account_no,
        is_paper=(config.mode == "vps"),
    )
    fetcher = KISDailyFetcher(KISDataProvider(auth))
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
    ws_monitor = WsHealthMonitor()
    api_monitor = Api5xxMonitor()

    telegram: TelegramClient | None = None
    if enable_telegram:
        telegram = TelegramClient(
            creds=config.telegram, transport=HttpxTransport(timeout=10.0)
        )

    trader = LiveTrader(
        config=config,
        fetcher=fetcher,
        cache=cache,
        bar_buffer=buffer,
        aggregator=aggregator,
        executor=executor,
        tracker=tracker,
        killswitch=killswitch,
        ws_monitor=ws_monitor,
        api_monitor=api_monitor,
        engines=list(engines),
        telegram=telegram,
        market_symbol=market_symbol,
    )

    # KIS WS 인스턴스 + thread launcher
    ws = KISWebSocket.from_auth(auth, hts_id=hts_id)
    ws_launcher = WsThreadLauncher(ws=ws)

    # FillSubscriber 는 launcher 를 ws_provider 로 받음 (Protocol 호환)
    fill_subscriber = KISFillSubscriber(
        ws_provider=ws_launcher,
        tracker=tracker,
        killswitch=killswitch,
        ws_monitor=ws_monitor,
        api_monitor=api_monitor,
        telegram=telegram,
        today=today,
    )

    return LiveSession(
        trader=trader, ws_launcher=ws_launcher, fill_subscriber=fill_subscriber
    )
