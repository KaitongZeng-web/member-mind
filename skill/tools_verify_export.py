#!/usr/bin/env python3
"""针对导出的实测：复现 Terra 主审场景（NOT 内的 TRUE 叶子不得写成“未满足”）。

用与复现目录同型的表头（会员编号/会员名称/所在城市/主营行业/资质标签/续费情况/
联系意愿/企业登记状态）与同型条件树（AND 含 NOT 与 OR，外加硬排除），
通过真实 CLI（import --mock → filter）跑一遍，然后检查 JSON / MD / CSV 三方口径。
"""
import csv
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(ROOT, "scripts", "ms_cli.py")

HEADER = ["会员编号", "会员名称", "所在城市", "主营行业", "资质标签",
          "续费情况", "联系意愿", "企业登记状态"]
ROWS = [
    ["M001", "企业甲（虚构）", "深圳", "工业传感器", "高新技术企业", "已到期未续费", "可联系", "存续"],
    ["M002", "企业乙（虚构）", "东莞", "自动化设备", "高新技术企业", "未续费", "可联系", "存续"],
    ["M003", "企业己（虚构）", "深圳", "贸易", "高新技术企业", "未续费", "可联系", "存续"],
    ["M004", "企业庚（虚构）", "广州", "工业传感器", "", "未续费", "可联系", "存续"],
    ["M005", "企业辛（虚构）", "东莞", "工业传感器", "", "已续费", "可联系", "存续"],
    ["M006", "企业丙（虚构）", "深圳", "工业传感器", "高新技术企业", "未续费", "可联系", "注销"],
    ["M007", "企业丁（虚构）", "深圳", "工业传感器", "高新技术企业", "未续费", "拒绝联系", "存续"],
    ["M008", "企业壬（虚构）", "深圳", "工业传感器", "", "未续费", "", "存续"],
    ["M009", "企业癸（虚构）", "深圳", "", "", "未续费", "可联系", "存续"],
]

REQUEST = {
    "rule_version": "terra-mock-20261031",
    "rule_is_mock": True,
    "conditions": {"logic": "and", "conditions": [
        {"field": "custom:所在城市", "op": "in", "value": ["深圳", "东莞"]},
        {"logic": "or", "conditions": [
            {"field": "custom:主营行业", "op": "eq", "value": "工业传感器"},
            {"field": "custom:资质标签", "op": "contains", "value": "高新技术企业"}]},
        {"logic": "not", "conditions": [
            {"field": "custom:主营行业", "op": "eq", "value": "贸易"}]},
        {"field": "custom:续费情况", "op": "in", "value": ["未续费", "已到期未续费"]},
        {"field": "custom:联系意愿", "op": "eq", "value": "可联系"}]},
    "hard_exclusions": [
        {"field": "custom:企业登记状态", "op": "eq", "value": "注销"},
        {"field": "custom:联系意愿", "op": "eq", "value": "拒绝联系"}],
}


def run(args, workdir):
    r = subprocess.run([sys.executable, CLI, "--workdir", workdir] + args,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("CLI 失败 rc=%s: %s" % (r.returncode, r.stderr))
    return r.stdout


def main():
    workdir = tempfile.mkdtemp(prefix="member-export-verify-")
    src = os.path.join(workdir, "terra_members.csv")
    with open(src, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEADER)
        w.writerows(ROWS)
    req = os.path.join(workdir, "req.json")
    with open(req, "w", encoding="utf-8") as f:
        json.dump(REQUEST, f, ensure_ascii=False)

    imp = json.loads(run(["import", "--file", src, "--mock"], workdir))
    print("import: mode=%s is_mock=%s counts=%s" % (imp["mode"], imp["is_mock"], imp["counts"]))

    flt = json.loads(run(["filter", "--request", "@" + req], workdir))
    print("filter: mode=%s counts=%s result_id=%s"
          % (flt["mode"], flt["counts"], flt["result_id"]))

    md_path = os.path.join(workdir, flt["outputs"]["md"])
    csv_path = os.path.join(workdir, flt["outputs"]["csv"])
    md = open(md_path, encoding="utf-8").read()
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))

    problems = []
    for e in flt["excluded"] + flt["needs_review"]:
        if e["reason_detail"] not in md:
            problems.append("MD 缺少 reason_detail: %s" % e["entity_id"])
        if e["reason_detail"] not in open(csv_path, encoding="utf-8").read():
            problems.append("CSV 缺少 reason_detail: %s" % e["entity_id"])
        row = next(r for r in rows if r["entity_id"] == e["entity_id"])
        if row["reason_detail"] != e["reason_detail"]:
            problems.append("CSV/JSON 不一致: %s" % e["entity_id"])
    if "未满足" in md or "未满足" in open(csv_path, encoding="utf-8").read():
        problems.append("仍存在误导性文案“未满足”")

    ji = next(e for e in flt["excluded"] if e["name"] == "企业己（虚构）")
    print("\n企业己（虚构）JSON reason_detail:", ji["reason_detail"])
    print("企业己（虚构）CSV reason_detail:",
          next(r for r in rows if r["entity_id"] == ji["entity_id"])["reason_detail"])
    print("\nMD 排除段：")
    print("\n".join(l for l in md.splitlines()
                    if l.startswith("| E-") and ("不满足" in l or "命中" in l)))

    print("\n工作目录：", workdir)
    print("MD:", flt["outputs"]["md"], "| CSV:", flt["outputs"]["csv"])
    if problems:
        print("\n实测未通过：")
        for p in problems:
            print("  -", p)
        return 1
    print("\n实测通过：JSON/MD/CSV 三方一致，无“未满足”误导文案。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
