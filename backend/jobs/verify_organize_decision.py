#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工单 02 的验证：整理器「判定」层必须与真实规则、真实摄入结果对得上。

为什么这五项证据一起才算数：
    判定错一个文件的后果是**无声的**（`daily_detail` 只增不减，被移走文件贡献的行
    会永久冻结成僵尸行/历史缺口，页面上完全看不出来）。所以不能只验「决策清单长得对」——
    还必须验它和**批处理侧真实算出的 key**、**真实摄入进库的行**三者一致。
    前三项覆盖「方案对不对」，后两项覆盖「前提成不成立」（源表没被动过、
    换掉判据就会被抓住）。

五项证据（与工单 02 的验收项一一对应）：
    compare <夹具目录>      ① dry-run 决策逐条 == fixtures.json 的 expect（0 处不一致），
                              并与工单 01 的独立 oracle 逐条对齐（含 key 计数）
    invariant <夹具目录> <db> ② 跨层不变量：
                              ⋃(keep 明细文件的 key) ∩ 白名单 == daily_detail 的 (date,shop,spu)
    idempotent <夹具目录>   ③ 连续两次 dry-run 输出逐字节相同
    nochange <源表根目录>   ④ dry-run 前后源表目录的文件名 + mtime + 大小完全不变；
                              也没有凭空冒出归档区 / 隔离区
    negative <夹具目录>     ⑤ 反向验证：把判据换成「日期窗被覆盖即归档」，
                              必须有夹具的决策翻转成错的（证明这套断言真的有牙齿）
    selftest                在临时目录里把上面五项全部跑一遍 + 判定纯函数的针对性断言，
                            聚合计数，全部通过才退出码 0

风格：沿用项目既有验证脚本（`verify_organize.py`）——独立脚本 + 自制断言计数器 + sys.exit(1)。
⛔ 不引入 pytest（requirements.txt 是全量 freeze，加测试框架会破坏异地复现的前提）。
⛔ 本脚本不改任何生产代码、不写 ResourceData、不搬移任何文件。

用法（必须用项目 venv 的解释器，依赖 polars / openpyxl）：
    .venv/Scripts/python.exe backend/jobs/verify_organize_decision.py selftest
    .venv/Scripts/python.exe backend/jobs/verify_organize_decision.py compare %TEMP%/org_fx
    .venv/Scripts/python.exe backend/jobs/verify_organize_decision.py nochange ResourceData
"""
from __future__ import annotations

import argparse
import gc
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 把仓库根目录塞进 sys.path：直接 `python backend/jobs/verify_organize_decision.py` 也能导入 backend 包
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.jobs import config  # noqa: E402
from backend.jobs import organize  # noqa: E402
from backend.jobs import verify_organize as vo  # noqa: E402
from backend.jobs.sources.excel_source import _norm_spu, load_spu_whitelist  # noqa: E402

TABLE_DIR_NAME = config.POP_TABLE_DIR_NAME

# 反向验证用的**临时**脚本模板（写进临时目录、随临时目录一起删除；⛔ 不落地到仓库）。
# 它把「key 覆盖」判据换成「日期跨度被同店同类池的并集覆盖即归档」——
# 这正是 spec 里说的「故意注入一个 bug（例如把 key 覆盖判据换回日期窗判定）」。
NEGATIVE_SCRIPT = '''# -*- coding: utf-8 -*-
"""反向验证用的临时脚本（⛔ 不入库，不构成第二套判据）。只读，不改任何文件。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "__REPO_ROOT__")
from backend.jobs import organize


def _span(keys):
    """把 key 集合压成日期跨度（这就是「只看日期窗」的那个错误判据）。"""
    if not keys:
        return None
    ds = sorted({k[1] for k in keys})
    return (ds[0], ds[-1])


def _covered(span, spans):
    """span 是否被 spans 的并集完全覆盖（ISO 日期串直接比较；有缝不衔接）。"""
    cur = None
    for a, b in sorted(s for s in spans if s):
        if cur is None or a > cur[1]:
            if cur is not None and cur[0] <= span[0] and span[1] <= cur[1]:
                return True
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    return bool(cur) and cur[0] <= span[0] and span[1] <= cur[1]


def main():
    root = Path(sys.argv[1])
    files = organize.collect_file_keys(root)
    shops = organize.load_contributing_shops(root)
    wrong = {}
    for name in sorted(files):
        info = files[name]
        if info["shop"] not in shops or not info["keys"]:
            continue
        spans = [_span(files[n]["keys"]) for n in sorted(files)
                 if n != name and files[n]["shop"] == info["shop"]
                 and files[n]["kind"] == info["kind"] and files[n]["keys"]]
        wrong[name] = "archive" if _covered(_span(info["keys"]), spans) else "keep"
    inv = json.loads((root / "fixtures.json").read_text(encoding="utf-8"))
    expect = {Path(f["rel_path"]).name: f["expect"] for f in inv["files"]}
    flips = [{"filename": n, "expect": expect.get(n), "wrong_decision": d}
             for n, d in sorted(wrong.items()) if expect.get(n) != d]
    print(json.dumps({"flips": flips, "n": len(flips)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
'''


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _expect_map(fixture_root: Path) -> dict[str, tuple[str, str]]:
    """读 fixtures.json，返回 {文件名: (expect, case)}。"""
    inv = json.loads((fixture_root / "fixtures.json").read_text(encoding="utf-8"))
    return {Path(f["rel_path"]).name: (f["expect"], f["case"]) for f in inv["files"]}


def _run_cli(source: Path, out: Path) -> subprocess.CompletedProcess:
    """在子进程里跑整理器的真 CLI（`python -m backend.jobs.organize`），返回完成对象。"""
    return subprocess.run(
        [sys.executable, "-m", "backend.jobs.organize",
         "--source", str(source), "--out", str(out)],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _fingerprint(table_dir: Path) -> list[tuple[str, bool, int, int]]:
    """给目录做指纹：每个条目的 (相对名, 是否目录, mtime_ns, 大小)。

    为什么记 mtime 而不只记文件名：搬移 / 重命名都会改 mtime，
    只比名字会漏掉「内容被动过」这一类改动。
    """
    if not table_dir.is_dir():
        return []
    out = []
    for p in sorted(table_dir.rglob("*")):
        st = p.stat()
        out.append((p.relative_to(table_dir).as_posix(), p.is_dir(), st.st_mtime_ns,
                    st.st_size if p.is_file() else 0))
    return out


def _daily_detail_keys(db_path: Path) -> set[tuple[str, str, str]]:
    """读出 daily_detail 的 (date, shop, spu) 集合。"""
    cells, _ = vo.read_db_cells(db_path)
    return {tuple(k.split("|")) for k in cells}


# --------------------------------------------------------------------------
# 证据 ①：决策清单 vs fixtures.json 的 expect vs 工单 01 的独立 oracle
# --------------------------------------------------------------------------

def compare_decisions(fixture_root: Path, ck: vo.Checker | None = None) -> dict:
    """逐条比对 dry-run 决策与 `fixtures.json` 的 `expect`，并与独立 oracle 对齐。

    Args:
        fixture_root: 夹具根目录（含 fixtures.json 与「数据源表目录」）。
        ck: 断言计数器；None 表示只打印不计数（供人工查看用）。

    Returns:
        dict: {"rows", "mismatch", "oracle_mismatch", "lock_present", "counts"}。
    """
    decisions = organize.derive_decisions(fixture_root)
    by_name = {d["filename"]: d for d in decisions}
    expect = _expect_map(fixture_root)
    inv = json.loads((fixture_root / "fixtures.json").read_text(encoding="utf-8"))
    oracle = {r["filename"]: r for r in vo.derive_fixture_decisions(fixture_root)}

    print(f"--- ① 决策比对：{fixture_root}（夹具 inventory {len(expect)} 条）---")
    print(f"{'文件':<54} {'expect':<10} {'实得':<10} {'keys':>5} {'oracle keys':>11}")
    mismatch: list[str] = []
    oracle_mismatch: list[str] = []
    compared = 0
    lock_present: list[str] = []
    for name in sorted(expect):
        want, case = expect[name]
        if name.startswith(organize.LOCK_PREFIX):
            # 锁文件不参与 key 计算 → 不该出现在决策清单里（「跳过」= 压根不读它）
            if name in by_name:
                lock_present.append(name)
            print(f"{name:<54} {'skip(锁)':<10} {'不参与计算':<10} {'—':>5} {'—':>11}")
            continue
        got = by_name.get(name)
        compared += 1
        got_dec = got["decision"] if got else "<缺失>"
        got_keys = got["key_count"] if got else -1
        ora_keys = oracle[name]["key_count"] if name in oracle else -1
        print(f"{name:<54} {want:<10} {got_dec:<10} {got_keys:>5} {ora_keys:>11}")
        if got is None:
            mismatch.append(f"{name}：未出现在决策清单里（期望 {want}；场景 {case}）")
            continue
        if got_dec != want:
            mismatch.append(f"{name}：期望 {want}，实得 {got_dec}（场景 {case}）")
        # 与独立 oracle 的 key 计数对齐 —— 这就是「两侧算出的 key 一致」的针对性证据
        if name not in oracle:
            oracle_mismatch.append(f"{name}：独立 oracle 里没有它")
        elif ora_keys != got_keys:
            oracle_mismatch.append(f"{name}：keys 计数 {got_keys} != oracle {ora_keys}")
        if name in oracle and oracle[name]["decision"] != got_dec:
            oracle_mismatch.append(
                f"{name}：决策 {got_dec} != oracle {oracle[name]['decision']}")

    extra = sorted(set(by_name) - set(expect))
    counts = {k: sum(1 for d in decisions if d["decision"] == k)
              for k in (organize.KEEP, organize.ARCHIVE,
                        organize.QUARANTINE, organize.SKIP)}
    print(f"决策统计：keep {counts[organize.KEEP]} / archive {counts[organize.ARCHIVE]} / "
          f"quarantine {counts[organize.QUARANTINE]} / skip {counts[organize.SKIP]}"
          f"（共 {len(decisions)} 条决策）")
    print(f"比对条数：{compared} 条决策 + 1 条锁文件缺席断言；不一致 {len(mismatch)} 处；"
          f"与 oracle 不一致 {len(oracle_mismatch)} 处")

    if ck is not None:
        ck.check(not mismatch, f"决策与 expect 逐条一致（{compared} 条，0 处不一致）",
                 "; ".join(mismatch)[:600])
        ck.check(not oracle_mismatch,
                 "决策与 key 计数都与工单 01 的独立 oracle 对齐",
                 "; ".join(oracle_mismatch)[:600])
        ck.check(not lock_present, "~$ 锁文件未参与 key 计算（等价于 skip）",
                 "; ".join(lock_present))
        ck.check(not extra, "决策清单里没有多余文件", "; ".join(extra)[:200])
    return {"rows": compared, "mismatch": mismatch, "oracle_mismatch": oracle_mismatch,
            "lock_present": lock_present, "counts": counts}


# --------------------------------------------------------------------------
# 证据 ②：跨层不变量
# --------------------------------------------------------------------------

def check_cross_layer_invariant(fixture_root: Path, db_path: Path,
                                ck: vo.Checker | None = None) -> dict:
    """断言「保留集的 key（∩ 白名单）」与「真实摄入进库的行」逐 key 相等。

    这条不变量最能抓「整理器与批处理两侧 key 规则漂移」：
    两侧若各写一套 key 提取，归档判定一旦按整理器那套算，就会把批处理还在用的行判成冗余。

    Args:
        fixture_root: 夹具根目录。
        db_path: 临时 SQLite 路径（会被创建/覆盖）。
        ck: 断言计数器。

    Returns:
        dict: {"kept": 保留集 key 数, "db": 库内 key 数, "keep_detail_files": n}。
    """
    files = organize.collect_file_keys(fixture_root)
    decisions = organize.decide(files, organize.load_contributing_shops(fixture_root))
    keep_detail = [d for d in decisions
                   if d["decision"] == organize.KEEP and d["kind"] == organize.KIND_DETAIL]

    kept: set = set()
    for d in keep_detail:
        kept |= files[d["filename"]]["keys"]

    whitelist = load_spu_whitelist(fixture_root)
    if whitelist:
        # 与 `PopSpuExcelSource.fetch()` 完全一致的过滤口径（店铺名 strip + SPU 归一）
        allowed = {(s.strip(), _norm_spu(sp)) for s, sps in whitelist.items() for sp in sps}
        kept = {k for k in kept if (k[0].strip(), _norm_spu(k[2])) in allowed}

    vo._ingest_into(fixture_root, db_path)  # 真实摄入路径（与批处理同一条）
    actual = _daily_detail_keys(db_path)
    expected = {(k[1], k[0], k[2]) for k in kept}  # key 是 (shop,date,spu)，库侧是 (date,shop,spu)

    print(f"--- ② 跨层不变量：keep 明细文件 {len(keep_detail)} 份 / "
          f"白名单店铺 {len(whitelist)} 个 ---")
    print(f"⋃(keep 明细文件 keys) ∩ 白名单 = {len(expected)} 个 (date,shop,spu)")
    print(f"daily_detail 里 (date,shop,spu)   = {len(actual)} 个")
    only_kept = sorted(expected - actual)[:3]
    only_db = sorted(actual - expected)[:3]
    print(f"差集：仅在保留集 {len(expected - actual)} 个 {only_kept}；"
          f"仅在库内 {len(actual - expected)} 个 {only_db}")

    if ck is not None:
        ck.check(expected == actual,
                 f"两侧 key 集合逐 key 相等（各 {len(expected)} 个）",
                 f"仅在保留集 {only_kept}；仅在库内 {only_db}")
        ck.check(len(actual) > 0, "库内确有数据（防止「两边都空」的假绿）")
    return {"kept": len(expected), "db": len(actual), "keep_detail_files": len(keep_detail)}


# --------------------------------------------------------------------------
# 证据 ③：幂等
# --------------------------------------------------------------------------

def check_idempotent(source: Path, tmp: Path, ck: vo.Checker | None = None) -> dict:
    """连续两次 dry-run，输出必须逐字节相同。"""
    a, b = tmp / "dryrun_1.json", tmp / "dryrun_2.json"
    r1, r2 = _run_cli(source, a), _run_cli(source, b)
    same = a.is_file() and b.is_file() and a.read_bytes() == b.read_bytes()
    inproc_same = organize.derive_decisions(source) == organize.derive_decisions(source)
    print(f"--- ③ 幂等：{source} ---")
    print(f"CLI 退出码：{r1.returncode} / {r2.returncode}；"
          f"两份清单字节数：{a.stat().st_size if a.exists() else -1} / "
          f"{b.stat().st_size if b.exists() else -1}")
    print(f"逐字节相同：{same}；进程内两次 derive_decisions 相等：{inproc_same}")

    if ck is not None:
        ck.check(r1.returncode == 0 and r2.returncode == 0, "两次 dry-run 都正常退出",
                 (r1.stderr or r2.stderr or "")[:300])
        ck.check(same, "连续两次 dry-run 输出逐字节相同")
        ck.check(inproc_same, "进程内连续两次 derive_decisions 结果相同")
    return {"same": same, "inproc_same": inproc_same}


# --------------------------------------------------------------------------
# 证据 ④：源表目录零改动
# --------------------------------------------------------------------------

def check_nochange(source: Path, tmp: Path, ck: vo.Checker | None = None) -> dict:
    """dry-run 前后源表目录必须零改动（文件名 + mtime + 大小）。"""
    table_dir = source / TABLE_DIR_NAME
    before = _fingerprint(table_dir)
    proc = _run_cli(source, tmp / "dryrun_nochange.json")
    after = _fingerprint(table_dir)
    added_dirs = [d for d in ("_archive", "_quarantine")
                  if (source / d).exists()]

    print(f"--- ④ 源表目录零改动：{table_dir} ---")
    print(f"dry-run 前 {len(before)} 个条目 → 后 {len(after)} 个条目；"
          f"指纹完全一致：{before == after}；CLI 退出码：{proc.returncode}")
    if before != after:
        for x, y in zip(before, after):
            if x != y:
                print(f"  差异：{x} → {y}")
                break

    if ck is not None:
        ck.check(proc.returncode == 0, "dry-run 正常退出", (proc.stderr or "")[:300])
        ck.check(before == after, "源表目录的文件名 + mtime + 大小完全未变")
        ck.check(not added_dirs, "源表根下没有冒出归档区 / 隔离区", str(added_dirs))
    return {"before": len(before), "after": len(after), "changed": before != after}


# --------------------------------------------------------------------------
# 证据 ⑤：反向验证（把判据换成日期窗，必须被抓住）
# --------------------------------------------------------------------------

def check_negative(fixture_root: Path, tmp: Path, ck: vo.Checker | None = None) -> dict:
    """跑一次「日期窗判据」的反向验证：至少有一份夹具的决策必须翻转成错的。

    脚本写进临时目录（随临时目录删除，⛔ 不入库），它读同一套真实 key、
    只把判据换成「日期跨度被并集覆盖即归档」——如果连它都判对了，
    说明这套断言没牙齿（夹具里根本没有能区分两种判据的样本）。
    """
    script = tmp / "_negative_date_window.py"
    script.write_text(NEGATIVE_SCRIPT.replace("__REPO_ROOT__", str(ROOT)), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(script), str(fixture_root)],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    print(f"--- ⑤ 反向验证：把判据换成「日期窗被覆盖即归档」（临时脚本，⛔ 不入库）---")
    print(f"临时脚本：{script}（本次运行后随临时目录删除）")
    data = {"flips": [], "n": -1}
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            pass
    for f in data["flips"]:
        print(f"  翻转：{f['filename']}  正确决策={f['expect']} → 日期窗判据给出={f['wrong_decision']}")
    print(f"被抓住的翻转份数：{data['n']}")

    if ck is not None:
        ck.check(proc.returncode == 0, "反向验证脚本正常退出",
                 (proc.stderr or "").strip()[:300])
        ck.check(data["n"] >= 1, f"错误判据至少翻转 1 份夹具（实际 {data['n']} 份）",
                 "夹具里没有能区分 key 覆盖与日期窗的样本 → 断言没牙齿")
    return data


# --------------------------------------------------------------------------
# 附加：判定纯函数的针对性断言（无 I/O、可直接 import 后断言）
# --------------------------------------------------------------------------

def check_pure_decide(ck: vo.Checker) -> None:
    """对 `decide()` 做几个针对性断言，证明它是无 I/O 纯函数且判据无误。

    覆盖五个最容易写错的点：同类池（混池会误归档推广）、空 key 隔离、
    零贡献店铺跳过、输出与输入顺序无关，以及**保留集判据的判别性用例**
    （两份 key 完全相同且无第三方 → 恰好一份归档、一份保留）。
    """
    k = ("S", "2025-01-01", "1")
    detail_a = {"shop": "S", "kind": "detail", "keys": {k}}
    detail_b = {"shop": "S", "kind": "detail", "keys": {k}}
    promo = {"shop": "S", "kind": "promo", "keys": {k}}

    # 🔴 判别性用例（旧判据「keys(F) ⊆ ⋃keys(别的文件)」下**两份都会归档**）：
    #    两份文件的 key 完全相同、且**没有第三方**供着它们。正确的保留集判据只会归档
    #    其中一份（同分取文件名更靠后者保留），⛔ 绝不能两份都归档——那样这批 key 全丢。
    #    这条用例是「判据里必须是最终保留集，而不是所有其他文件」的最小复现。
    twins = {("S", "2026-01-01", "1"), ("S", "2026-01-02", "1")}
    got_twins = {d["filename"]: d["decision"] for d in organize.decide(
        {"twins_a.xlsx": {"shop": "S", "kind": "detail", "keys": set(twins)},
         "twins_b.xlsx": {"shop": "S", "kind": "detail", "keys": set(twins)}}, {"S"})}
    ck.check(sorted(got_twins.values()) == [organize.ARCHIVE, organize.KEEP],
             "判别性用例：两份 key 完全相同且无第三方 → 恰好一份归档、一份保留（绝不两份都归档）",
             f"实际 {got_twins}")
    ck.check(got_twins.get("twins_b.xlsx") == organize.KEEP
             and got_twins.get("twins_a.xlsx") == organize.ARCHIVE,
             "同分取文件名更靠后者保留（twins_b 保留、twins_a 归档）", f"实际 {got_twins}")

    # 混池陷阱：明细两份互相覆盖 → 归档一份留一份；推广虽然 key 一样，但池里只有它自己 → 必须保留
    got = {d["filename"]: d["decision"] for d in
           organize.decide({"a.xlsx": detail_a, "b.xlsx": detail_b, "p.csv": promo}, {"S"})}
    ck.check(got["p.csv"] == organize.KEEP
             and sorted(got.values()) == [organize.ARCHIVE, organize.KEEP, organize.KEEP],
             "同类池：推广文件不会被「明细已覆盖同 key」误归档", f"实际 {got}")

    # 空 key → 隔离（且必须排在覆盖判据之前，否则空集会被判成可归档）
    empty = {"shop": "S", "kind": "detail", "keys": set()}
    got2 = {d["filename"]: d["decision"] for d in
            organize.decide({"e.xlsx": empty, "a.xlsx": detail_a}, {"S"})}
    ck.check(got2 == {"e.xlsx": organize.QUARANTINE, "a.xlsx": organize.KEEP},
             "提不出 key 的文件判隔离、且不影响同池其他文件", f"实际 {got2}")

    # 零贡献店铺 → 整组跳过（哪怕它其实持有独有 key）
    got3 = organize.decide({"a.xlsx": detail_a}, set())[0]
    ck.check(got3["decision"] == organize.SKIP, "零贡献店铺整组跳过", got3["reason"])

    # 纯函数：不碰文件系统，且输出与输入顺序无关
    keys = [("S", "2025-01-01", "1"), ("S", "2025-01-02", "1")]
    f1 = {"a.xlsx": {"shop": "S", "kind": "detail", "keys": set(keys)},
          "b.xlsx": {"shop": "S", "kind": "detail", "keys": {keys[0]}}}
    rev = dict(reversed(list(f1.items())))
    ck.check(organize.decide(f1, {"S"}) == organize.decide(rev, {"S"}),
             "判定结果与输入字典顺序无关（纯函数）")

    # skip 的判定先于隔离：读失败但店铺零贡献 → skip（与工单 01 oracle 的顺序一致）
    bad = {"shop": "空贡献店", "kind": "detail", "keys": set(), "error": "Boom: x"}
    ck.check(organize.decide({"z.xlsx": bad}, {"S"})[0]["decision"] == organize.SKIP,
             "零贡献店铺的读失败文件仍判 skip（判定顺序与 oracle 一致）")


def check_full_spu_scope(fixture_root: Path, ck: vo.Checker | None = None) -> None:
    """证明 key 提取取**全量 SPU**、不受白名单过滤（验收项之一）。

    结构上的保证：`decide()` 的入参里只有「店铺名集合」，压根拿不到 SPU 白名单，
    所以它不可能按 SPU 过滤。但结构保证必须被实证钉住——夹具里「空贡献店」的文件
    含 SPU 9999（白名单未登记它），提取时照样要算出全部 30 个 key。
    """
    files = organize.collect_file_keys(fixture_root)
    whitelist = load_spu_whitelist(fixture_root)
    allowed = {(s.strip(), _norm_spu(sp)) for s, sps in whitelist.items() for sp in sps}
    outside = {k for info in files.values() for k in info["keys"]
               if (k[0].strip(), _norm_spu(k[2])) not in allowed}
    target = "空贡献店_商品明细_2025-01-01_2025-01-30.xlsx"
    got = len(files[target]["keys"]) if target in files else -1

    print("--- 附加：key 提取取全量 SPU（不受白名单过滤）---")
    print(f"白名单外的 (shop, spu) 样本：{sorted({(k[0], k[2]) for k in outside})}")
    print(f"{target} 的 key 数：{got}")

    if ck is not None:
        ck.check(outside, "提出来的 key 里含白名单外的 (shop,spu) → 提取确实没过白名单")
        ck.check(target in files and got == 30,
                 "白名单外店铺的文件照样算出全部 30 个 key", f"实际 {got}")
        # 行为断言（不碰文档措辞）：只持有「白名单外 SPU」的文件，仍是 keep——
        # 若判据偷偷按白名单裁 key，它就会被判成 archive 或 quarantine
        k = ("S", "2025-01-01", "0000-白名单外SPU")
        got2 = {d["filename"]: d["decision"] for d in organize.decide(
            {"a.xlsx": {"shop": "S", "kind": "detail", "keys": {k}},
             "b.xlsx": {"shop": "S", "kind": "detail", "keys": {("S", "2025-01-01", "1")}}}, {"S"})}
        ck.check(got2.get("a.xlsx") == organize.KEEP,
                 "只持有白名单外 SPU 的文件仍判 keep → 判据取全量 SPU", f"实际 {got2}")


# --------------------------------------------------------------------------
# 命令实现
# --------------------------------------------------------------------------

def cmd_compare(args) -> int:
    ck = vo.Checker("工单 02 证据①：dry-run 决策 vs expect vs 独立 oracle")
    compare_decisions(Path(args.fixture_root), ck)
    return ck.report()


def cmd_invariant(args) -> int:
    ck = vo.Checker("工单 02 证据②：跨层不变量（保留集 key == daily_detail）")
    check_cross_layer_invariant(Path(args.fixture_root), Path(args.db), ck)
    return ck.report()


def cmd_idempotent(args) -> int:
    ck = vo.Checker("工单 02 证据③：dry-run 幂等")
    tmp = Path(tempfile.mkdtemp(prefix="verify_org_dec_idem_"))
    try:
        check_idempotent(Path(args.source), tmp, ck)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ck.report()


def cmd_nochange(args) -> int:
    ck = vo.Checker("工单 02 证据④：源表目录零改动")
    tmp = Path(tempfile.mkdtemp(prefix="verify_org_dec_nc_"))
    try:
        check_nochange(Path(args.source), tmp, ck)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ck.report()


def cmd_negative(args) -> int:
    ck = vo.Checker("工单 02 证据⑤：反向验证（日期窗判据必须被抓住）")
    tmp = Path(tempfile.mkdtemp(prefix="verify_org_dec_neg_"))
    try:
        check_negative(Path(args.fixture_root), tmp, ck)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ck.report()


def cmd_selftest(args) -> int:
    """在临时目录里把五项证据全部跑一遍（含判定纯函数的断言）。"""
    ck = vo.Checker("工单 02 自证：判定层与真实规则 / 真实摄入 / 真实目录都对得上")
    tmp = Path(tempfile.mkdtemp(prefix="verify_organize_decision_"))
    try:
        # —— 夹具（clean：基线可摄入；full：多一份缺 SPU 列的形态异常文件）——
        clean = tmp / "fx_clean"
        inv_clean = vo.make_fixtures(clean, clean=True)
        ck.check(not vo.verify_fixture_oracle(clean, inv_clean),
                 "clean 夹具的 oracle 自检通过（底座可信）")

        # —— ① 决策表 ——
        r1 = compare_decisions(clean, ck)
        ck.check(r1["rows"] == 19,
                 f"clean 模式比对 19 条决策（实际 {r1['rows']}）",
                 f"inventory={len(_expect_map(clean))}")
        ck.check(r1["counts"] == {organize.KEEP: 12, organize.ARCHIVE: 5,
                                  organize.QUARANTINE: 1, organize.SKIP: 1},
                 f"clean 模式决策统计精确匹配（实际 {r1['counts']}）")

        # —— ② 跨层不变量 ——
        inv = check_cross_layer_invariant(clean, tmp / "app.db", ck)
        ck.check(inv["keep_detail_files"] >= 1, "保留集里确有明细文件（否则不变量是空对空）")

        # —— ③ 幂等 ——
        check_idempotent(clean, tmp, ck)

        # —— ④ 零改动 ——
        check_nochange(clean, tmp, ck)

        # —— ⑤ 反向验证 + 还原 ——
        check_negative(clean, tmp, ck)
        r_restore = compare_decisions(clean, ck)  # 还原：正确判据下仍然 0 处不一致
        ck.check(not r_restore["mismatch"], "反向验证后正确判据仍然全对（还原成功）")

        # —— 附加：判定纯函数的针对性断言 + 全量 SPU 口径 ——
        check_pure_decide(ck)
        check_full_spu_scope(clean, ck)

        # —— full 模式：多一份「缺 SPU 列」形态异常文件，必须为 quarantine ——
        full = tmp / "fx_full"
        inv_full = vo.make_fixtures(full, clean=False)
        ck.check(not vo.verify_fixture_oracle(full, inv_full), "full 夹具的 oracle 自检通过")
        r_full = compare_decisions(full, ck)
        ck.check(r_full["rows"] == 20, f"full 模式比对 20 条决策（实际 {r_full['rows']}）",
                 f"inventory={len(_expect_map(full))}")
        ck.check(r_full["counts"] == {organize.KEEP: 12, organize.ARCHIVE: 5,
                                      organize.QUARANTINE: 2, organize.SKIP: 1},
                 f"full 模式决策统计精确匹配（实际 {r_full['counts']}）")
        broken = "钻芯旗舰店_商品明细_2025-03-01_2025-03-30.xlsx"
        got_broken = next((d for d in organize.derive_decisions(full)
                           if d["filename"] == broken), None)
        ck.check(got_broken is not None and got_broken["decision"] == organize.QUARANTINE,
                 f"full 模式下缺 SPU 列那份判 quarantine（{broken}）",
                 str(got_broken))
        ck.check(got_broken is not None and "ColumnNotFoundError" in got_broken["reason"],
                 "隔离理由带上了真实异常（不猜规则）",
                 got_broken["reason"] if got_broken else "")
    finally:
        # ⚠️ 已知发现（工单 01 Comments #4，⛔ 本工单不改生产代码）：
        # excel_source.load_spu_whitelist / load_spu_name_map 用 read_only 打开的 workbook
        # 没有 close()，openpyxl 的引用环只能靠循环 GC 回收 → Windows 上「文件仍被占用」，
        # 临时目录一次删不掉。这里先 gc.collect() 再删。
        gc.collect()
        shutil.rmtree(tmp, ignore_errors=True)
        ck.check(not tmp.exists(), "临时目录已清理（先 gc.collect() 才删得掉，见工单 01 Comments #4）")
    return ck.report()


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    p = argparse.ArgumentParser(
        prog="verify_organize_decision.py",
        description="工单 02 验证：整理器判定层的决策清单 / 跨层不变量 / 幂等 / 零改动 / 反向验证。",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compare", help="① dry-run 决策 vs fixtures.json 的 expect")
    c.add_argument("fixture_root", help="夹具根目录（含 fixtures.json）")
    c.set_defaults(func=cmd_compare)

    i = sub.add_parser("invariant", help="② 跨层不变量：保留集 key vs daily_detail")
    i.add_argument("fixture_root")
    i.add_argument("db", help="临时 SQLite 路径（会被创建/覆盖）")
    i.set_defaults(func=cmd_invariant)

    d = sub.add_parser("idempotent", help="③ 连续两次 dry-run 逐字节相同")
    d.add_argument("source", help="数据源根目录")
    d.set_defaults(func=cmd_idempotent)

    n = sub.add_parser("nochange", help="④ dry-run 前后源表目录零改动")
    n.add_argument("source", help="数据源根目录")
    n.set_defaults(func=cmd_nochange)

    g = sub.add_parser("negative", help="⑤ 反向验证：日期窗判据必须被抓住")
    g.add_argument("fixture_root")
    g.set_defaults(func=cmd_negative)

    t = sub.add_parser("selftest", help="在临时目录里跑完五项证据")
    t.set_defaults(func=cmd_selftest)
    return p


def main(argv: list[str] | None = None) -> int:
    """脚本入口：解析子命令并返回退出码。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文会乱码
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
