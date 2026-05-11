"""LiveConfig 의 KIS 자격증명을 ~/KIS/config/kis_devlp.yaml 의 paper_*/my_* 필드로
동기화. backtester/kis_auth.py 는 module-level 에서 yaml 을 한 번만 읽으므로
호출자는 이 함수를 'kis_auth import 전' 에 실행해야 한다.

기존 yaml 은 .backup-{epoch} 로 백업.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Optional

import yaml

from kis_backtest.live.config.credentials import KISCreds


def sync_kis_yaml(
    kis: KISCreds, yaml_path: Optional[Path] = None
) -> Optional[Path]:
    """KIS 자격증명을 kis_devlp.yaml 로 동기화. backup 경로 반환 (있을 때)."""
    yaml_path = yaml_path or Path.home() / "KIS" / "config" / "kis_devlp.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"KIS yaml not found: {yaml_path}")

    backup = yaml_path.with_name(f"{yaml_path.name}.backup-{int(time.time())}")
    shutil.copy(str(yaml_path), str(backup))

    with open(yaml_path, encoding="UTF-8") as f:
        cfg = yaml.safe_load(f) or {}

    if kis.mode == "vps":
        cfg["paper_app"] = kis.appkey
        cfg["paper_sec"] = kis.appsecret
        cfg["my_paper_stock"] = kis.account_no
    elif kis.mode == "prod":
        cfg["my_app"] = kis.appkey
        cfg["my_sec"] = kis.appsecret
        cfg["my_acct_stock"] = kis.account_no
    else:
        raise ValueError(f"kis.mode must be vps/prod, got {kis.mode!r}")

    with open(yaml_path, "w", encoding="UTF-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, default_flow_style=False)
    return backup
