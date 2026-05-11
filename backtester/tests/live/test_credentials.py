from __future__ import annotations

from pathlib import Path

import pytest

from kis_backtest.live.config.credentials import (
    KISCreds,
    LiveConfig,
    MissingEnvError,
    TelegramCreds,
    TradingLimits,
)


@pytest.fixture
def env_full() -> dict[str, str]:
    return {
        "COMPOSITE_TRADER_MODE": "vps",
        "COMPOSITE_TRADER_CAPITAL_KRW": "5000000",
        "TG_BOT_TOKEN": "bot:abc",
        "TG_CHAT_ID": "12345",
        "KIS_VPS_APPKEY": "vps-key",
        "KIS_VPS_APPSECRET": "vps-secret",
        "KIS_VPS_ACCOUNT_NO": "12345678-01",
    }


class TestTelegramCreds:
    def test_from_env_dict(self, env_full):
        c = TelegramCreds.from_env(env_full)
        assert c.bot_token == "bot:abc"
        assert c.chat_id == "12345"

    def test_missing_token(self, env_full):
        env_full.pop("TG_BOT_TOKEN")
        with pytest.raises(MissingEnvError, match="TG_BOT_TOKEN"):
            TelegramCreds.from_env(env_full)

    def test_empty_chat_id(self, env_full):
        env_full["TG_CHAT_ID"] = ""
        with pytest.raises(MissingEnvError, match="TG_CHAT_ID"):
            TelegramCreds.from_env(env_full)

    def test_repr_masks_bot_token(self):
        c = TelegramCreds(bot_token="123456789:secret-token-suffix-1234", chat_id="999")
        rendered = repr(c)
        assert "secret-token" not in rendered
        assert "123456789" not in rendered
        assert "***1234" in rendered  # 마지막 4자만 노출
        assert "999" in rendered  # chat_id 는 정상 표시


class TestKISCreds:
    def test_vps_keys(self, env_full):
        c = KISCreds.from_env("vps", env_full)
        assert c.appkey == "vps-key"
        assert c.mode == "vps"

    def test_prod_requires_prod_prefix(self, env_full):
        env_full.update(
            {
                "KIS_PROD_APPKEY": "prod-key",
                "KIS_PROD_APPSECRET": "prod-secret",
                "KIS_PROD_ACCOUNT_NO": "9999-01",
            }
        )
        c = KISCreds.from_env("prod", env_full)
        assert c.appkey == "prod-key"
        assert c.mode == "prod"

    def test_invalid_mode(self, env_full):
        with pytest.raises(ValueError, match="vps or prod"):
            KISCreds.from_env("test", env_full)

    def test_prod_missing_when_only_vps_set(self, env_full):
        with pytest.raises(MissingEnvError, match="KIS_PROD_APPKEY"):
            KISCreds.from_env("prod", env_full)

    def test_repr_masks_appkey_appsecret(self, env_full):
        env_full["KIS_VPS_APPKEY"] = "PSXXXXXXXXXXXXXXX1234"
        env_full["KIS_VPS_APPSECRET"] = "supersecret-secret-secret-5678"
        c = KISCreds.from_env("vps", env_full)
        rendered = repr(c)
        assert "PSXXXXXXXXXXXXXXX" not in rendered
        assert "supersecret" not in rendered
        assert "***1234" in rendered
        assert "***5678" in rendered


class TestTradingLimits:
    def test_defaults_applied(self, env_full):
        limits = TradingLimits.from_env(env_full)
        assert limits.capital_krw == 5_000_000
        assert limits.daily_loss_pct == 3.0
        assert limits.cumulative_loss_pct == 8.0

    def test_custom_overrides(self, env_full):
        env_full["COMPOSITE_TRADER_DAILY_LOSS_PCT"] = "2.5"
        env_full["COMPOSITE_TRADER_CUMULATIVE_LOSS_PCT"] = "6.0"
        limits = TradingLimits.from_env(env_full)
        assert limits.daily_loss_pct == 2.5
        assert limits.cumulative_loss_pct == 6.0

    def test_zero_capital_rejected(self, env_full):
        env_full["COMPOSITE_TRADER_CAPITAL_KRW"] = "0"
        with pytest.raises(ValueError, match="positive"):
            TradingLimits.from_env(env_full)

    def test_missing_capital(self, env_full):
        env_full.pop("COMPOSITE_TRADER_CAPITAL_KRW")
        with pytest.raises(MissingEnvError, match="CAPITAL_KRW"):
            TradingLimits.from_env(env_full)


class TestLiveConfig:
    def test_from_env_dotfile(self, tmp_path: Path, env_full):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "\n".join(f"{k}={v}" for k, v in env_full.items()), encoding="utf-8"
        )
        cfg = LiveConfig.from_env(env_path)
        assert cfg.mode == "vps"
        assert cfg.telegram.bot_token == "bot:abc"
        assert cfg.kis.mode == "vps"
        assert cfg.limits.capital_krw == 5_000_000

    def test_invalid_mode(self, tmp_path: Path, env_full):
        env_full["COMPOSITE_TRADER_MODE"] = "test"
        env_path = tmp_path / ".env"
        env_path.write_text(
            "\n".join(f"{k}={v}" for k, v in env_full.items()), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="vps or prod"):
            LiveConfig.from_env(env_path)

    def test_nonexistent_file_falls_back_to_os_environ(self, tmp_path: Path, monkeypatch, env_full):
        for k, v in env_full.items():
            monkeypatch.setenv(k, v)
        cfg = LiveConfig.from_env(tmp_path / "missing.env")
        assert cfg.mode == "vps"
