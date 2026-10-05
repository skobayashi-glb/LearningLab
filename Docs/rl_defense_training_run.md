# 防御力を含むキャラ自動調整の実行結果

実行日: 2026-09-23。操作AIは固定し、キャラ数値だけを探索した。PPOの操作学習は行っていない。

## 結論

**24候補・合計1,920試合を実行した。一括適用するとエンゼルフィッシュ混成の評価が悪化したため、最終推奨は実験の基準値を維持する。**

強弱の割り当てファイルは変更していない。ゲームアセットへ数値を書き戻していない。ここでいう基準値はcharacter_balance_plan由来の実験用初期案であり、現在のUnityアセット値と同一とは限らない。

## 条件

- 対象: amago / goblin / ammonite / shark / angelfish / sea-slug / coelacanth / abyssal-sea-dragonの8体。
- 単体・混成の16case。各分割で4seed。
- HP・攻撃力は既存の役割別候補を使用。防御は壁役0/25/50、火力・状態役0/10/20。
- 強弱指定、コスト、速度、技、状態効果、敵の数値を固定。
- 通常攻撃のみ防御で軽減。状態異常・状態反応ダメージは防御無視。
- trainはOpenField、validationはChokePoint。holdoutには新設のRL_DefenseHoldout（2つの通路を持つ迂回地形）を使用。
- seedはtrain 10101〜10104、validation 11101〜11104、holdout 12101〜12104。分割間で重複なし。

## スコア

小さいほど、役割・強弱別の設定目標に近い。勝率そのものではない。

| 評価 | 基準値 | 探索で選んだ候補 |
|---|---:|---:|
| train（変更量の罰則込み） | 8.49575 | 8.00715 |
| validation（対象キャラだけ変更） | 11.14322 | 10.26407 |
| validation（全候補を同時適用） | 11.14322 | 10.09242 |

対象キャラだけを変える評価では16caseすべてが非悪化だった。しかし、全キャラを同時に変更するとangelfish-mixedの損失が3.65477から4.06239へ悪化した。全体平均の改善で個別編成の悪化を隠さない条件により、一括採用を見送った。

## 見つかった候補（未採用）

| キャラ | HP：基準→候補 | 攻撃力：基準→候補 | 防御：基準→候補 |
|---|---:|---:|---:|
| amago | 22→20 | 3→3 | 0→0 |
| goblin | 20→18 | 6→6 | 0→0 |
| ammonite | 120→132 | 3→3 | 0→0 |
| shark | 60→60 | 12→12 | 0→20 |
| angelfish | 30→30 | 3→3 | 0→0 |
| sea-slug | 24→22 | 2→2 | 0→0 |
| coelacanth | 154→169 | 30→30 | 0→10 |
| abyssal-sea-dragon | 210→189 | 108→124 | 0→20 |

この表は診断用で、製品への採用値ではない。最終推奨の防御力は全対象で0のまま。

## 最終確認と試合数

一括採用を見送った後、基準値を新しいholdoutで64試合評価した。損失は3.54647。別ステージなのでtrain/validationの数値と単純比較して改善率を計算しない。

| 処理 | 試合数 |
|---|---:|
| 24候補の探索 | 1,536 |
| 基準・候補の個別validation | 128 |
| 旧フローが先に計算した候補holdout（選択には不使用） | 64 |
| 基準・候補の同時適用validation | 128 |
| 基準値維持後の最終holdout | 64 |
| 合計 | 1,920 |

実行中に同時適用での相互作用を検出したため、自動調整フローを修正した。今後は個別validationに加えて同時適用validationを通した後に推奨値を決め、最後にholdoutを実行する。今回先に計算された候補holdoutは採否に使っていない。

4seedは小規模な探索であり、強弱の順位や適正な目標帯の確証ではない。今回のholdoutは確認済みになったため、次の設計変更の最終確認には新しい未閲覧データを用意する。Unity本体との戦闘一致も未検証。

## 設定・結果・再実行

- 設定: LearningLab/configs/defense_training_plan.json / defense_training_stages.json
- ユーザー指定の強弱: LearningLab/configs/strength_assignments.json
- 凍結入力: LearningLab/runs/defense-training-campaign-20260923/
- 最終判定: LearningLab/runs/defense-training-search-20260923/final-review.json
- 最終推奨config: 同フォルダーのfinal-recommended-config.json（基準値を維持）
- 保存用結果: LearningLab/results/defense_training_20260923.json

個別評価時点の出力はindividual-report.jsonとindividual-recommended-config.jsonへ保存した。report.jsonとrecommended-config.jsonは、同時適用の検査後の最終判定に更新済み。final-review.jsonとfinal-recommended-config.jsonにも同じ最終判定を保存している。

```powershell
cd LearningLab
python -m learning_lab.campaign --plan configs/defense_training_plan.json --template configs/defense_training_stages.json --strengths configs/strength_assignments.json --output runs/defense-next-input
python -m learning_lab.balance --config runs/defense-next-input/experiment.json --settings runs/defense-next-input/targets.json --run runs/defense-next-search
```

上のコマンドは同条件の再現用。改訂後の自動フローでは同時適用validationを含めて最終推奨をreport.jsonへ出す。候補探索と個別評価は再利用せず最初から実行する。