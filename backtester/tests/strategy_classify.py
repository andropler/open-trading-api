#!/usr/bin/env python3
"""Sweep 결과를 받아 전략을 Production / Research / Deprecated 로 분류하고
markdown 리포트 (CLASSIFICATION.md)를 작성한다.

기본 sweep 결과(`phase1_daily_topN_*.csv`)와 그리드 결과(`grid_topN_*.csv`)를 모두 읽어
- 디폴트 메트릭 vs 그리드 최적 메트릭을 비교
- 분류 기준 적용
- 폐기 사유는 "기본 + 그리드 모두 부적합"이어야 함

사용:
    uv run python tests/strategy_classify.py
"""

from __future__ import annotations

import argparse
import glob
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
SWEEP_DIR = REPO_ROOT / "examples" / "output" / "sweep"
OUT_PATH = SWEEP_DIR / "CLASSIFICATION.md"

logger = logging.getLogger("classify")


# 분류 임계값 (8년 기준)
PROD_SHARPE = 0.9
PROD_MDD = 0.25
PROD_PF = 1.5
PROD_MIN_TRADES = 100

RESEARCH_SHARPE = 0.3
RESEARCH_MIN_TRADES = 50


def load_phase1(pattern: str = "phase1_daily_top*_*.csv") -> Optional[pd.DataFrame]:
    files = sorted(SWEEP_DIR.glob(pattern))
    if not files:
        return None
    f = files[-1]
    logger.info("Phase 1 sweep: %s", f.name)
    return pd.read_csv(f)


def load_grid(pattern: str = "grid_top*_*.csv") -> Optional[pd.DataFrame]:
    files = sorted(SWEEP_DIR.glob(pattern))
    if not files:
        return None
    f = files[-1]
    logger.info("Grid sweep: %s", f.name)
    return pd.read_csv(f)


def best_grid_per_strategy(grid_df: pd.DataFrame) -> pd.DataFrame:
    """각 전략별 sharpe_ratio가 최대인 그리드 포인트."""
    if grid_df is None or grid_df.empty:
        return pd.DataFrame()
    df = grid_df[grid_df["success"] == True].copy()
    if df.empty:
        return pd.DataFrame()
    df["sharpe_ratio"] = pd.to_numeric(df["sharpe_ratio"], errors="coerce")
    df = df.dropna(subset=["sharpe_ratio"])
    idx = df.groupby("strategy_id")["sharpe_ratio"].idxmax()
    return df.loc[idx].reset_index(drop=True)


def classify(metrics: Dict[str, float]) -> Tuple[str, str]:
    """단일 후보(디폴트 또는 그리드 최적)를 분류."""
    sharpe = metrics.get("sharpe_ratio")
    mdd = metrics.get("max_drawdown")
    pf = metrics.get("profit_factor")
    trades = metrics.get("total_trades")

    if pd.isna(sharpe) or pd.isna(mdd) or pd.isna(pf) or pd.isna(trades):
        return "Failed", "버그/실패"

    if (
        sharpe >= PROD_SHARPE
        and mdd <= PROD_MDD
        and pf >= PROD_PF
        and trades >= PROD_MIN_TRADES
    ):
        return "Production", f"Sharpe {sharpe:.2f} ≥ {PROD_SHARPE}, MDD {mdd:.1%} ≤ {PROD_MDD:.0%}, PF {pf:.2f} ≥ {PROD_PF}, trades {int(trades)} ≥ {PROD_MIN_TRADES}"

    if sharpe >= RESEARCH_SHARPE and trades >= RESEARCH_MIN_TRADES:
        return "Research", f"Sharpe {sharpe:.2f}, MDD {mdd:.1%}, PF {pf:.2f}, trades {int(trades)} — Production 기준 미달"

    if trades < RESEARCH_MIN_TRADES:
        return "Deprecated", f"거래 부족 (trades={int(trades)} < {RESEARCH_MIN_TRADES})"

    return "Deprecated", f"성과 미달 (Sharpe {sharpe:.2f} < {RESEARCH_SHARPE} 또는 PF {pf:.2f}/MDD {mdd:.1%})"


def final_verdict(default_tier: str, grid_tier: str) -> str:
    """디폴트와 그리드 최적 중 더 높은 등급으로."""
    rank = {"Production": 3, "Research": 2, "Deprecated": 1, "Failed": 0}
    return default_tier if rank.get(default_tier, 0) >= rank.get(grid_tier, 0) else grid_tier


def fmt_pct(v) -> str:
    if pd.isna(v):
        return "-"
    return f"{v*100:+.1f}%"


def fmt_num(v, fmt="{:.2f}") -> str:
    if pd.isna(v):
        return "-"
    return fmt.format(v)


def fmt_int(v) -> str:
    if pd.isna(v):
        return "-"
    return f"{int(v):,}"


def render_markdown(
    phase1_df: Optional[pd.DataFrame],
    grid_df: Optional[pd.DataFrame],
    grid_best_df: pd.DataFrame,
    v41_baseline: Optional[Dict[str, float]] = None,
) -> str:
    lines: List[str] = []
    lines.append("# 전략 분류 리포트")
    lines.append("")
    lines.append(f"_생성: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}_")
    lines.append("")

    # 분류 기준
    lines.append("## 분류 기준 (8년 기준)")
    lines.append("")
    lines.append(
        f"- **Production**: Sharpe ≥ {PROD_SHARPE}, MDD ≤ {PROD_MDD*100:.0f}%, PF ≥ {PROD_PF}, 거래수 ≥ {PROD_MIN_TRADES}"
    )
    lines.append(
        f"- **Research**: Sharpe ≥ {RESEARCH_SHARPE}, 거래수 ≥ {RESEARCH_MIN_TRADES} — 추가 연구로 살릴 여지"
    )
    lines.append("- **Deprecated**: 위 두 조건 모두 미달 (디폴트 + 그리드 최적 모두 실패해야 폐기 확정)")
    lines.append("")

    if phase1_df is not None:
        kospi = phase1_df["kospi_return_pct"].iloc[0]
        lines.append(f"## Phase 1 — 일봉 8년 sweep (KOSPI {fmt_pct(kospi)})")
        lines.append("")
        lines.append(
            "| 전략 | 수익률 | CAGR | Sharpe | MDD | 거래수 | Win Rate | **PF** | KOSPI 초과 | 분류 |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")

        rank = {"Production": 3, "Research": 2, "Deprecated": 1, "Failed": 0}
        rows_with_tier = []

        for _, row in phase1_df.iterrows():
            metrics = {
                "sharpe_ratio": row["sharpe_ratio"],
                "max_drawdown": row["max_drawdown"],
                "profit_factor": row["profit_factor"],
                "total_trades": row["total_trades"],
            }
            tier_default, _ = classify(metrics)

            grid_tier = "-"
            grid_metrics = None
            if not grid_best_df.empty:
                gmatch = grid_best_df[grid_best_df["strategy_id"] == row["strategy_id"]]
                if not gmatch.empty:
                    g = gmatch.iloc[0]
                    grid_metrics = {
                        "sharpe_ratio": g["sharpe_ratio"],
                        "max_drawdown": g["max_drawdown"],
                        "profit_factor": g["profit_factor"],
                        "total_trades": g["total_trades"],
                    }
                    grid_tier, _ = classify(grid_metrics)

            verdict = final_verdict(tier_default, grid_tier if grid_tier != "-" else tier_default)

            tier_display = verdict
            if tier_default != verdict and grid_tier == verdict:
                tier_display = f"{verdict} (그리드 통과)"

            rows_with_tier.append((
                rank.get(verdict, 0),
                row["strategy_id"],
                f"| {row['strategy_id']} | {fmt_pct(row['total_return_pct'])} | "
                f"{fmt_pct(row['cagr'])} | {fmt_num(row['sharpe_ratio'])} | "
                f"{fmt_pct(row['max_drawdown'])} | {fmt_int(row['total_trades'])} | "
                f"{fmt_pct(row['win_rate'])} | **{fmt_num(row['profit_factor'])}** | "
                f"{fmt_pct(row['excess_vs_kospi'])} | {tier_display} |",
                verdict,
                grid_metrics,
                grid_tier,
            ))

        # 분류 등급 내림차순으로 정렬
        rows_with_tier.sort(key=lambda x: (-x[0], x[1]))
        for _, _, line, _, _, _ in rows_with_tier:
            lines.append(line)

        lines.append("")

        # 그리드 최적이 디폴트보다 좋은 경우만 표시
        if not grid_best_df.empty:
            lines.append("## 그리드 최적 vs 디폴트")
            lines.append("")
            lines.append(
                "디폴트로 미달했던 전략을 그리드 서치로 살릴 수 있는지 검증."
            )
            lines.append("")
            lines.append(
                "| 전략 | 디폴트 Sharpe | 그리드 최적 Sharpe | 최적 파라미터 | 그리드 PF | 그리드 거래수 |"
            )
            lines.append("|---|---:|---:|---|---:|---:|")
            for _, row in phase1_df.iterrows():
                gmatch = grid_best_df[grid_best_df["strategy_id"] == row["strategy_id"]]
                if gmatch.empty:
                    continue
                g = gmatch.iloc[0]
                params_str = f"{g['param_name1']}={g['param_value1']}, {g['param_name2']}={g['param_value2']}"
                lines.append(
                    f"| {row['strategy_id']} | {fmt_num(row['sharpe_ratio'])} | "
                    f"{fmt_num(g['sharpe_ratio'])} | {params_str} | "
                    f"{fmt_num(g['profit_factor'])} | {fmt_int(g['total_trades'])} |"
                )
            lines.append("")

    if v41_baseline is not None:
        lines.append("## 5분봉/인트라데이 — V4.1 1H baseline")
        lines.append("")
        lines.append(
            f"기존 검증된 alpha-hunter KR 1H Breakout V4.1 (top 15 종목, 3 동시 포지션, 1H 봉)"
        )
        lines.append("")
        for k, v in v41_baseline.items():
            lines.append(f"- **{k}**: {v}")
        lines.append("")
        lines.append(
            "_5분봉 sweep은 LeanClient 캐시 메커니즘이 일봉 전용이라 별도 인프라 필요 — Phase 2로 보류._"
        )
        lines.append("")

    # 분류 정리
    lines.append("## 최종 분류")
    lines.append("")

    if phase1_df is not None:
        prod_list, research_list, dep_list, failed_list = [], [], [], []
        for _, row in phase1_df.iterrows():
            metrics = {
                "sharpe_ratio": row["sharpe_ratio"],
                "max_drawdown": row["max_drawdown"],
                "profit_factor": row["profit_factor"],
                "total_trades": row["total_trades"],
            }
            tier_d, reason_d = classify(metrics)
            grid_tier, reason_g = "-", "-"
            if not grid_best_df.empty:
                gmatch = grid_best_df[grid_best_df["strategy_id"] == row["strategy_id"]]
                if not gmatch.empty:
                    g = gmatch.iloc[0]
                    gm = {
                        "sharpe_ratio": g["sharpe_ratio"],
                        "max_drawdown": g["max_drawdown"],
                        "profit_factor": g["profit_factor"],
                        "total_trades": g["total_trades"],
                    }
                    grid_tier, reason_g = classify(gm)
            verdict = final_verdict(tier_d, grid_tier if grid_tier != "-" else tier_d)

            entry = f"- **{row['strategy_id']}** — {reason_d if verdict == tier_d else reason_g}"
            if verdict == "Production":
                prod_list.append(entry)
            elif verdict == "Research":
                research_list.append(entry)
            elif verdict == "Failed":
                failed_list.append(entry)
            else:
                dep_list.append(entry)

        lines.append(f"### 🏆 Production ({len(prod_list)}개)")
        lines.extend(prod_list or ["- (없음)"])
        lines.append("")
        lines.append(f"### 🔬 Research ({len(research_list)}개)")
        lines.extend(research_list or ["- (없음)"])
        lines.append("")
        lines.append(f"### 🗑️ Deprecated ({len(dep_list)}개)")
        lines.extend(dep_list or ["- (없음)"])
        lines.append("")
        if failed_list:
            lines.append(f"### ⚠️ Failed/버그 ({len(failed_list)}개)")
            lines.extend(failed_list)
            lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="전략 분류 리포트 생성")
    parser.add_argument("--phase1-pattern", default="phase1_daily_top*_*.csv")
    parser.add_argument("--grid-pattern", default="grid_top*_*.csv")
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    phase1_df = load_phase1(args.phase1_pattern)
    grid_df = load_grid(args.grid_pattern)
    grid_best_df = best_grid_per_strategy(grid_df) if grid_df is not None else pd.DataFrame()

    md = render_markdown(phase1_df, grid_df, grid_best_df)
    out = Path(args.out)
    out.write_text(md)
    logger.info("리포트 작성: %s (%d bytes)", out, len(md))


if __name__ == "__main__":
    main()
