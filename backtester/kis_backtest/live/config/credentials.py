"""환경변수 기반 자격증명·운영 설정 로더.

.env 파일(python-dotenv) 또는 os.environ에서 읽어 frozen dataclass로 검증된
객체를 반환한다. env 인자(dict)가 가장 높은 우선순위. 필수 키 누락·빈 문자열
시 어떤 키인지 명시한 MissingEnvError로 즉시 실패한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class MissingEnvError(RuntimeError):
    """필수 환경변수 누락·빈 문자열일 때 발생."""


def _load_dotenv(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def _required(env: Mapping[str, str], key: str) -> str:
    val = env.get(key) or os.environ.get(key)
    if not val:
        raise MissingEnvError(f"required env var missing or empty: {key}")
    return val


def _optional(env: Mapping[str, str], key: str, default: str) -> str:
    val = env.get(key) or os.environ.get(key)
    return val if val else default


@dataclass(frozen=True)
class TelegramCreds:
    bot_token: str
    chat_id: str

    def __repr__(self) -> str:
        # bot_token 노출 방지 — 로그/예외 trace에 평문 토큰이 찍히지 않도록 마스킹.
        suffix = self.bot_token[-4:] if len(self.bot_token) >= 4 else "***"
        return f"TelegramCreds(bot_token='***{suffix}', chat_id={self.chat_id!r})"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TelegramCreds":
        env = env or {}
        return cls(
            bot_token=_required(env, "TG_BOT_TOKEN"),
            chat_id=_required(env, "TG_CHAT_ID"),
        )


@dataclass(frozen=True)
class KISCreds:
    appkey: str
    appsecret: str
    account_no: str
    mode: str

    def __repr__(self) -> str:
        # appkey/appsecret 노출 방지 — 마지막 4자만 표시.
        ks = f"***{self.appkey[-4:]}" if len(self.appkey) >= 4 else "***"
        ss = f"***{self.appsecret[-4:]}" if len(self.appsecret) >= 4 else "***"
        return (
            f"KISCreds(appkey='{ks}', appsecret='{ss}', "
            f"account_no={self.account_no!r}, mode={self.mode!r})"
        )

    @classmethod
    def from_env(cls, mode: str, env: Mapping[str, str] | None = None) -> "KISCreds":
        if mode not in ("vps", "prod"):
            raise ValueError(f"mode must be vps or prod, got {mode!r}")
        env = env or {}
        prefix = "KIS_VPS" if mode == "vps" else "KIS_PROD"
        return cls(
            appkey=_required(env, f"{prefix}_APPKEY"),
            appsecret=_required(env, f"{prefix}_APPSECRET"),
            account_no=_required(env, f"{prefix}_ACCOUNT_NO"),
            mode=mode,
        )


@dataclass(frozen=True)
class TradingLimits:
    capital_krw: int
    daily_loss_pct: float
    cumulative_loss_pct: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TradingLimits":
        env = env or {}
        capital = int(_required(env, "COMPOSITE_TRADER_CAPITAL_KRW"))
        if capital <= 0:
            raise ValueError(f"COMPOSITE_TRADER_CAPITAL_KRW must be positive, got {capital}")
        daily = float(_optional(env, "COMPOSITE_TRADER_DAILY_LOSS_PCT", "3.0"))
        cumulative = float(_optional(env, "COMPOSITE_TRADER_CUMULATIVE_LOSS_PCT", "8.0"))
        if daily <= 0 or cumulative <= 0:
            raise ValueError(
                f"loss pct must be positive (daily={daily}, cumulative={cumulative})"
            )
        return cls(
            capital_krw=capital,
            daily_loss_pct=daily,
            cumulative_loss_pct=cumulative,
        )


@dataclass(frozen=True)
class LiveConfig:
    mode: str
    telegram: TelegramCreds
    kis: KISCreds
    limits: TradingLimits

    @classmethod
    def from_env(cls, env_path: Path | str | None = None) -> "LiveConfig":
        path = Path(env_path) if env_path else None
        env = _load_dotenv(path)
        mode = _required(env, "COMPOSITE_TRADER_MODE")
        if mode not in ("vps", "prod"):
            raise ValueError(f"COMPOSITE_TRADER_MODE must be vps or prod, got {mode!r}")
        return cls(
            mode=mode,
            telegram=TelegramCreds.from_env(env),
            kis=KISCreds.from_env(mode, env),
            limits=TradingLimits.from_env(env),
        )
