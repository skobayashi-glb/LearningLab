"""Build a reproducible role/identity campaign from authored character profiles."""
import argparse
import copy
import json
from pathlib import Path

from .artifacts import write_json
from .balance import BalanceSearch, VERSION as BALANCE_VERSION
from .config import ExperimentConfig, canonical_hash
from .export_battle import BattleExporter, enum_array

VERSION = 1
MINIMUM_STAT = 1
SCALE_MINIMUM = 0.1
PROFILE_FIELDS = ("hp", "power", "speed", "cost", "defense")
DEFAULT_STRENGTH_ASSIGNMENTS = Path(__file__).resolve().parents[1] / "configs/strength_assignments.json"
ROLE_FLAGS = {"attacker": 1, "tank": 2, "status": 16, "positioning": 64, "support": 12}
SAND_STATUS = 4
STRENGTH_SUPPORT_FACTORS = {"weak": 0.65, "standard": 1, "strong": 1.15, "conditional": 1}


def target_band(low, high, weight):
    return {"min": low, "max": high, "scale": max(SCALE_MINIMUM, high - low), "weight": weight}


def design_markdown(audit):
    strengths = {"weak": "弱め", "standard": "標準", "strong": "強め", "conditional": "編成依存"}
    roles = {"attacker": "火力", "tank": "壁", "status": "状態付与", "positioning": "位置操作", "support": "支援"}
    lines = ["# キャラの個性・役割・コスト・強弱の初期割り当て", "",
        "2026-09-23。ユーザーの委任に基づく実験用の初期設計。数値はゲームアセットへ未反映で、調整完了値ではない。", "",
        "強弱の正本はLearningLab/configs/strength_assignments.json。ユーザーが変更し、自動探索は割り当てを変更しない。この表は書き出し時点のスナップショット。", "",
        "強弱は主にコスト当たりの効率を意味する。高コストの弱めキャラが、低コストの強めキャラより絶対火力で低いとは限らない。",
        "弱めキャラにも固有の使い道を残す。技・状態・射程・攻撃周期・速度を全員共通に揃えない。",
        "既存の能力を基に個性を割り当てたもので、新しい技や挙動を実装した表ではない。", "",
        "## 初期パラメータと個性", "",
        "| キャラ | 役割 | 強弱 | コスト | HP | 攻撃力 | 防御力 | 速度 | 得意な使い方 | 残す弱点 | 今回の対象 |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---|---|"]
    for p in audit:
        status = "調整対象" if p["active"] else "次回候補" if p["supported"] else "未対応能力あり"
        lines.append(f"| {p['id']} | {roles[p['role']]} | {strengths[p['strength']]} | {p['cost']} | {p['hp']} | {p['power']} | {p.get('defense', 0)} | {p['speed']} | {p['identity']} | {p['weakness']} | {status} |")
    lines += ["", "## 自動調整で守る条件", "",
        "- 初期設計で速度・コストを定め、その後の探索では固定する。技・状態・射程・攻撃周期・既存役割も固定。",
        "- 探索は役割別の狭いHP・攻撃力候補に限定する。壁役は攻撃力を探索せず、HPだけを変える。",
        "- 防御力の初期値は0。defense_candidatesを明示したキャラだけ、防御力も探索する。通常攻撃だけを軽減し、状態異常・状態反応ダメージは防御を無視。",
        "- 役割別・強弱別に異なる指標の目標範囲を使い、全員の勝率や性能を同じにしない。",
        "- 状態付与役はダメージより混成編成での拠点保全を重くする。ただし補助貢献の因果推定ではなく代理指標。",
        "- 各キャラを単体編成と混成編成で評価する。混成の他キャラは初期設計値に固定し、評価対象だけに探索値を適用。",
        "- 最初の出撃で評価対象を優先し、参加しなかったキャラを弱いと誤判定しない。以後は固定パーティの枠順で巡回し、次のキャラのエナジーが貯まるまで待つ。",
        "- 敵は元のゲームデータを固定した別スナップショット。味方の初期再設計・探索値は敵へ波及しない。",
        "- 検証の全体損失または1つでも個別caseの損失が悪化した候補は採用しない。最終確認データは選択に使わない。", "",
        "## 評価上の限界", "",
        "目標帯は初期仮説であり、少数seedでは適正さも強弱の順位も証明できない。個性の説明文が異なるだけで、プレイ上の非重複を保証するものではない。",
        "固定操作での自動パラメータ探索を実行する。PPOによる操作AI学習は今回実行しない。",
        "単体と混成の両方を必須とするため、専門的な状態役がどの編成でも強い設計にはしない。役割別の目標自体も結果を見て再検討する。",
        "Unity本体との戦闘一致、移動・隊列・支援等の未対応範囲はLearningLab/BATTLE.mdを参照。", ""]
    return "\n".join(lines)


def apply_strength_assignments(plan, strengths):
    plan = copy.deepcopy(plan)
    expected = {p["id"] for p in plan["profiles"]}
    assignments = strengths["assignments"]
    if strengths.get("version") != VERSION or set(assignments) != expected:
        raise ValueError("Strength assignments must cover all characters exactly")
    if any(value not in plan["strength_bands"] for value in assignments.values()):
        raise ValueError("Unknown strength; use weak/standard/strong/conditional")
    for profile in plan["profiles"]:
        profile["strength"] = assignments[profile["id"]]
    return plan


def build_campaign(plan, template, exporter=None, strengths=None):
    exporter = exporter or BattleExporter()
    if strengths is None:
        strengths = json.loads(DEFAULT_STRENGTH_ASSIGNMENTS.read_text(encoding="utf-8-sig"))
    plan = apply_strength_assignments(plan, strengths)
    if plan["version"] != VERSION:
        raise ValueError("Unknown character plan version")
    profiles = {p["id"]: p for p in plan["profiles"]}
    if len(profiles) != len(plan["profiles"]) or set(profiles) != set(exporter.characters):
        raise ValueError("Plan must cover every current character exactly once")
    if len({p["identity"] for p in profiles.values()}) != len(profiles):
        raise ValueError("Duplicate character identity")
    active = plan["active_characters"]
    if not active or len(set(active)) != len(active) or set(active) - profiles.keys():
        raise ValueError("Invalid active character cohort")
    audit = []
    for character, profile in profiles.items():
        original = exporter.characters[character][1]
        if profile["role"] not in ROLE_FLAGS or not (original.get("role", 0) & ROLE_FLAGS[profile["role"]]):
            raise ValueError(f"Authored role does not match existing mechanics: {character}")
        if profile["strength"] not in plan["strength_bands"] or not profile["weakness"]:
            raise ValueError("Every character needs a strength assignment and weakness")
        profile.setdefault("defense", original.get("defense", 0))
        if (any(type(profile[k]) not in (int, float) or profile[k] <= 0 for k in PROFILE_FIELDS if k != "defense")
                or type(profile["defense"]) is not int or profile["defense"] < 0):
            raise ValueError("Invalid profile stat")
        try:
            definition = exporter.character(character)
            if SAND_STATUS in definition["statuses"]:
                raise ValueError("Sanded contact transmission is not supported")
            supported, reason = True, ""
        except (ValueError, KeyError) as error:
            supported, reason = False, str(error)
        if character in active and not supported:
            raise ValueError(f"Active character is unsupported: {character}: {reason}")
        audit.append({**profile, "active": character in active, "supported": supported, "reason": reason,
                      "original": {"hp": original["maxHp"], "power": original["attackPower"],
                                   "speed": original["moveSpeed"], "cost": original["deployCost"], "defense": original.get("defense", 0)},
                      "mechanics": {"role_flags": original["role"], "statuses": enum_array(original.get("appliedStatuses")),
                                    "shape": original["attackShape"]["type"], "range": original["attackRange"]}})
    setup = copy.deepcopy(template)
    all_ids = sorted(set(active) | set(plan["enemy_pool"]) | set(plan["mixed_anchors"]))
    for scenario in setup["scenarios"]:
        # The exporter includes definitions for all roster IDs; the evaluator installs each case roster.
        scenario["roster"] = all_ids
        scenario["enemy_pool"] = plan["enemy_pool"]
        scenario["ally_overrides"] = {c: {k: profiles[c][k] for k in PROFILE_FIELDS} for c in all_ids}
        scenario["rules"].update(plan["rules_overrides"])
    config = exporter.export(setup)
    settings = {"version": BALANCE_VERSION, "candidate_budget": plan["candidate_budget"], "search_seed": plan["search_seed"],
                "change_weight": plan["change_weight"], "seeds": plan["seeds"], "parameters": [], "cases": [],
                "character_plan_hash": canonical_hash(plan), "strength_assignments": copy.deepcopy(strengths)}
    for character in active:
        profile = profiles[character]
        role = plan["role_targets"][profile["role"]]
        for field in ("hp", "power"):
            values = sorted({max(MINIMUM_STAT, round(profile[field] * f)) for f in role[field + "_factors"]} | {profile[field]})
            if len(values) > 1:
                settings["parameters"].append({"character": character, "field": field, "values": values})
        if "defense_candidates" in profile:
            settings["parameters"].append({"character": character, "field": "defense", "values": profile["defense_candidates"]})
        low, high = plan["strength_bands"][profile["strength"]]
        multiplier = role["damage_multiplier"]
        support_factor = STRENGTH_SUPPORT_FACTORS[profile["strength"]]
        targets = {
            "damage_per_energy": target_band(low * multiplier, high * multiplier, role["damage_weight"]),
            "survival_fraction": target_band(role["survival"][0] * support_factor, role["survival"][1], role["survival_weight"]),
            "main_hp_ratio": target_band(role["main_hp"][0] * support_factor, role["main_hp"][1], role["main_hp_weight"])}
        solo = [character]
        mixed = list(dict.fromkeys([character, *plan["mixed_anchors"]]))
        for mode, roster in (("solo", solo), ("mixed", mixed)):
            settings["cases"].append({"id": f"{character}-{mode}", "character": character,
                "role": profile["role"], "strength": profile["strength"], "reference_cost": profile["cost"],
                "weight": 1, "roster": roster, "freeze_others": True, "target_first": True, "targets": targets})
    BalanceSearch(ExperimentConfig(config), settings)  # fail before writing an invalid campaign
    environment = ExperimentConfig(config).create_environment()
    try:
        for scenario in config["scenarios"]:
            environment.reset(scenario, config["seed"])
    finally:
        environment.close()
    return config, settings, audit


def main():
    parser = argparse.ArgumentParser(description="Prepare a character identity balance campaign")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--strengths", type=Path, default=DEFAULT_STRENGTH_ASSIGNMENTS)
    args = parser.parse_args()
    output = args.output.resolve()
    assets = Path(__file__).resolve().parents[2] / "Assets"
    if output.is_relative_to(assets.resolve()) or output.exists():
        raise ValueError("Use a new output directory outside Assets")
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    strengths = json.loads(args.strengths.read_text(encoding="utf-8-sig"))
    config, settings, audit = build_campaign(plan, template, strengths=strengths)
    write_json(output / "experiment.json", config)
    write_json(output / "targets.json", settings)
    write_json(output / "character-audit.json", audit)
    write_json(output / "plan.json", plan)
    write_json(output / "strength_assignments.json", strengths)
    (output / "character-design.md").write_text(design_markdown(audit), encoding="utf-8")
    print(json.dumps({"profiles": len(audit), "active": len(plan["active_characters"]),
                      "cases": len(settings["cases"]), "output": str(output)}))


if __name__ == "__main__":
    main()
