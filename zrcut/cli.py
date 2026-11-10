"""智刃工艺命令行入口。

用法示例：
    python -m zrcut.cli recommend --material M1 --tool T3 --op OP2 --ra 1.6
    python -m zrcut.cli validate --material M1 --tool T4 --vc 180 --fz 0.12 --ap 20 --ae 14
    python -m zrcut.cli card --part 底板 --blank 100x60x20 --material M1 --tool T3 --op OP2
    python -m zrcut.cli search "304 不锈钢 内冷"
    python -m zrcut.cli meta
    python -m zrcut.cli serve --port 8077
    python -m zrcut.cli eval
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from . import __version__
from .errors import ZrCutError
from .service import ServiceConfig, ZrCutService


def _dump(obj: Any) -> None:
    """以 UTF-8 友好方式打印 JSON。"""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    """构建参数解析器。"""
    p = argparse.ArgumentParser(
        prog="zrcut",
        description="智刃工艺 ZR-CUT：CNC 切削工艺参数智能推荐系统",
    )
    p.add_argument("--version", action="version", version=f"zrcut {__version__}")
    p.add_argument("--verbose", "-v", action="store_true", help="输出调试日志")
    p.add_argument("--llm", action="store_true", help="启用 LLM 叙述（需配置 API Key）")

    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("recommend", help="参数推荐")
    r.add_argument("--material", "-m", required=True, help="材料代号 M1-M5")
    r.add_argument("--tool", "-t", required=True, help="刀具代号 T1-T6")
    r.add_argument("--op", "-o", required=True, help="工序代号 OP1-OP4")
    r.add_argument("--ra", type=float, default=3.2, help="粗糙度要求 μm")
    r.add_argument("--strategy", choices=["EFFICIENCY", "QUALITY", "LIFE", "BALANCED"])
    r.add_argument("--volume", type=float, help="待去除体积 mm³")
    r.add_argument("--wall", type=float, help="工件壁厚 mm")
    r.add_argument("--no-coolant", action="store_true", help="不具备内冷")

    v = sub.add_parser("validate", help="硬约束校验与收敛")
    v.add_argument("--material", "-m", required=True)
    v.add_argument("--tool", "-t", required=True)
    v.add_argument("--vc", type=float, required=True)
    v.add_argument("--fz", type=float, required=True)
    v.add_argument("--ap", type=float, required=True)
    v.add_argument("--ae", type=float, required=True)
    v.add_argument("--ra", type=float, default=3.2)
    v.add_argument("--no-converge", action="store_true", help="不尝试自动收敛")

    pr = sub.add_parser("predict", help="三项预测")
    pr.add_argument("--material", "-m", required=True)
    pr.add_argument("--tool", "-t", required=True)
    pr.add_argument("--vc", type=float, required=True)
    pr.add_argument("--fz", type=float, required=True)
    pr.add_argument("--ap", type=float, required=True)
    pr.add_argument("--volume", type=float)

    c = sub.add_parser("card", help="生成工艺卡")
    c.add_argument("--part", required=True, help="零件名")
    c.add_argument("--blank", required=True, help="毛坯尺寸")
    c.add_argument("--material", "-m", required=True)
    c.add_argument("--tool", "-t", required=True)
    c.add_argument("--op", "-o", required=True)
    c.add_argument("--ra", type=float, default=3.2)
    c.add_argument("--op-no", type=int, default=10)
    c.add_argument("--volume", type=float)
    c.add_argument("--issue", metavar="ISSUER", help="生成后立即签发")

    s = sub.add_parser("search", help="知识检索")
    s.add_argument("query", help="查询文本")
    s.add_argument("--top-k", type=int, default=5)

    sub.add_parser("meta", help="打印元数据")
    sub.add_parser("eval", help="运行内置评估集")

    sv = sub.add_parser("serve", help="启动 HTTP API")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8077)

    return p


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。

    Args:
        argv: 参数列表，None 取 sys.argv。

    Returns:
        进程退出码：0 成功，1 业务错误，2 参数错误。
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.cmd == "serve":
        from .api import serve

        serve(
            host=args.host,
            port=args.port,
            config=ServiceConfig(enable_llm=args.llm),
        )
        return 0

    svc = ZrCutService(config=ServiceConfig(enable_llm=args.llm))

    try:
        if args.cmd == "recommend":
            _dump(
                svc.recommend(
                    material=args.material,
                    tool=args.tool,
                    operation=args.op,
                    ra_req=args.ra,
                    strategy=args.strategy,
                    volume=args.volume,
                    wall_thickness=args.wall,
                    coolant_available=not args.no_coolant,
                )
            )
        elif args.cmd == "validate":
            _dump(
                svc.validate_params(
                    material=args.material,
                    tool=args.tool,
                    vc=args.vc,
                    fz=args.fz,
                    ap=args.ap,
                    ae=args.ae,
                    ra_req=args.ra,
                    converge_attempt=not args.no_converge,
                )
            )
        elif args.cmd == "predict":
            _dump(
                svc.predict(
                    material=args.material,
                    tool=args.tool,
                    vc=args.vc,
                    fz=args.fz,
                    ap=args.ap,
                    volume=args.volume,
                )
            )
        elif args.cmd == "card":
            data = svc.create_card(
                part_name=args.part,
                blank_size=args.blank,
                op_no=args.op_no,
                material=args.material,
                tool=args.tool,
                operation=args.op,
                ra_req=args.ra,
                volume=args.volume,
            )
            if args.issue:
                svc.issue_card(data["cardId"], issued_by=args.issue)
                # 签发后重新读取，确保输出的 markdown 与锁定状态一致
                data = svc.get_card(data["cardId"])
            print(data["markdown"])
            print()
            _dump(
                {
                    "cardId": data["cardId"],
                    "status": data["status"],
                    "contentHash": data["contentHash"],
                    "qrPayload": data["qrPayload"],
                }
            )
        elif args.cmd == "search":
            _dump(svc.search(args.query, top_k=args.top_k))
        elif args.cmd == "meta":
            from .api import ApiHandler

            _dump(ApiHandler._meta())
        elif args.cmd == "eval":
            from .evaluate import run_builtin_eval

            report = run_builtin_eval(svc)
            _dump(report)
            return 0 if report.get("passed") else 1
        return 0
    except ZrCutError as exc:
        print(f"错误 {exc.code.value}: {exc.message}", file=sys.stderr)
        if exc.detail:
            print(f"详情: {json.dumps(exc.detail, ensure_ascii=False)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
