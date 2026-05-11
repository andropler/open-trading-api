"""자격증명 ping 테스트.

흐름:
1. backtester/.env.live 로드 (LiveConfig.from_env)
2. ~/KIS/config/kis_devlp.yaml 백업 + paper_app/paper_sec/my_paper_stock 갱신
3. KIS 모의(vps) 토큰 발급 (ka.auth)
4. 텔레그램 STARTUP 테스트 메시지 발송

실제 외부 API 호출이지만 read-only — 주문 발행 X.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_LIVE = REPO_ROOT / ".env.live"
YAML_PATH = Path.home() / "KIS" / "config" / "kis_devlp.yaml"


def main() -> int:
    # 1) .env.live 로드
    sys.path.insert(0, str(REPO_ROOT))
    from kis_backtest.live.config.credentials import LiveConfig

    if not ENV_LIVE.exists():
        print(f"[FAIL] .env.live not found at {ENV_LIVE}")
        return 1
    config = LiveConfig.from_env(ENV_LIVE)
    print(f"[1] LiveConfig loaded — mode={config.mode} capital={config.limits.capital_krw}")
    print(f"    kis={config.kis}")
    print(f"    telegram={config.telegram}")

    # 2) yaml 백업 + paper_* 갱신
    if not YAML_PATH.exists():
        print(f"[FAIL] {YAML_PATH} 없음 — KIS Open API 패키지가 설치되어야 함")
        return 1
    backup = YAML_PATH.with_name(f"kis_devlp.yaml.backup-{int(time.time())}")
    shutil.copy(YAML_PATH, backup)
    print(f"[2] yaml backup → {backup}")

    with open(YAML_PATH, encoding="UTF-8") as f:
        cfg = yaml.safe_load(f)
    cfg["paper_app"] = config.kis.appkey
    cfg["paper_sec"] = config.kis.appsecret
    cfg["my_paper_stock"] = config.kis.account_no
    with open(YAML_PATH, "w", encoding="UTF-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, default_flow_style=False)
    print("    yaml updated: paper_app, paper_sec, my_paper_stock")

    # 3) KIS 모의 토큰 발급 (yaml 갱신 이후에 import 해야 새 자격증명 반영)
    import kis_auth as ka

    try:
        ka.auth(svr="vps")
    except Exception as e:
        print(f"[3 FAIL] KIS vps auth 실패: {e}")
        return 2
    tr_env = ka.getTREnv()
    print(f"[3] KIS vps 인증 성공 base_url={tr_env.my_url}")
    print(f"    토큰 길이={len(tr_env.my_token) if tr_env.my_token else 0}")

    # 4) 텔레그램 ping
    from kis_backtest.live.notify.telegram import (
        Category,
        HttpxTransport,
        TelegramClient,
    )

    try:
        client = TelegramClient(
            creds=config.telegram, transport=HttpxTransport(timeout=10.0)
        )
        client.send(
            Category.STARTUP,
            "ping test — composite live trader credentials OK",
            strategy="composite",
            now=time.time(),
        )
    except Exception as e:
        print(f"[4 FAIL] 텔레그램 송신 실패: {e}")
        return 3
    print("[4] 텔레그램 메시지 발송 성공")

    print("\n[PING] all credentials verified ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
