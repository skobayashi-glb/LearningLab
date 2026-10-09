# キャラ数値の候補生成分布を学習する

2026-10-09、LearningLab 0.4.0。現行planの`search_strategy`は`adaptive-distribution`です。固定候補の一覧を抽選する方式から、範囲内の数値を生成する確率分布を対戦結果で更新する方式に変更しました。

## 1候補ごとの更新

```mermaid
flowchart LR
    D[候補生成分布] --> C[範囲内の数値を生成]
    C --> E[全trainケース・地形・seedで対戦]
    E --> S[目標帯との距離と変更量を集計]
    S --> U[分布の平均と広がりをAdamで更新]
    U --> D
```

最初に設計上の基準値を評価し、比較用スコアを初期化します。その後は候補1件を生成し、その候補の全train試合を終了してから1回更新します。次の候補は更新後の分布から生成します。`candidate_budget=24`なら基準値1件＋学習用候補23件、分布更新23回です。

1候補は対象キャラ全員の数値の組です。1試合ごとに数値を変える意味ではありません。防御planの通常設定では32case×2地形×4seed＝256試合を集計してから1回更新します。相方と敵のID列はcase・seedに固定し、候補ごとの抽選相手の違いを成績改善と取り違えないようにします。

## 設計者が決める範囲

| 項目 | 現行planの範囲・扱い |
| --- | --- |
| HP | 初期設計値の50〜150%。下限を切り上げ、上限を切り捨てた正の整数 |
| 攻撃力 | 火力・状態付与役は50〜150%の整数。壁・位置操作・支援役は固定 |
| 防御力 | 防御planで明示した8対象のみ。通常0〜100、壁役ammoniteは0〜200の整数 |
| 速度・出撃コスト | 初期設計値に固定 |
| 技・射程・周期・状態・役割・強弱ラベル | 既存の設計に固定 |
| 個性・弱点の説明文 | 設計用metadata。文章を損失に変換する処理はない |

例えばHP30〜90なら31、47、68なども候補になります。小さい攻撃力も、amagoの基準3なら2〜4、sea-slugの基準2なら1〜3を探索できます。整数項目の精度は1単位です。無制限な値を出す方式ではなく、設計者が変更できる上下限を守ります。

通常planはHP・攻撃力の15軸、防御planは防御8軸を加えた23軸です。各軸に学習可能な平均と対数標準偏差を持つため、学習パラメータはそれぞれ30個・46個です。42体中、数値を変更する対象は8体、相方と敵の抽選プールは能力監査済み30体です。

planの`role_targets.hp_range_factors`・`power_range_factors`、profileの`defense_range`からcampaignの`parameters.min/max`を生成します。直接設定する例:

```json
{
  "search_strategy": "adaptive-distribution",
  "parameters": [
    {"character": "shark", "field": "hp", "min": 30, "max": 90}
  ],
  "adaptive": {"learning_rate": 0.05, "initial_std": 1.0}
}
```

これは設定の抜粋です。実行にはcase・seed・候補数なども必要です。範囲内に全scenario共通の基準値があること、整数項目の境界が整数であることを検査します。`values`との混在は拒否します。

## モデルと損失

巨大なニューラルネットワークではなく、各数値軸に独立した潜在正規分布を持つモデルです。

```text
z_i ~ Normal(μ_i, exp(logσ_i))
fraction_i = sigmoid(z_i)
stat_i = min_i + (max_i - min_i) × fraction_i
整数項目はstat_iを整数へ丸める
```

初期平均は基準値の範囲内位置から作り、境界上の基準値は初期位置を10〜90%に制限します。防御0が基準でも探索が止まらないようにするためです。基準候補自体は必ず防御0で最初に評価します。潜在標準偏差の初期値は1、下限0.15、上限2.5です。

戦闘シミュレーションの計算グラフに逆伝播する必要はありません。サンプルした潜在値を定数として、その対数確率を微分するscore-function（REINFORCE）推定を使います。PyTorchの[score-functionの説明](https://docs.pytorch.org/docs/2.14/distributions.html#score-function)に対応する方式です。丸め後の離散数値についても潜在サンプル上の推定を使えます。

```text
S = train評価損失 + change_weight × 変更量罰則
A = clip((S - reference) / max(abs(reference), 1), -10, 10)
L = A × Σ log Normal(z_i; μ_i, σ_i) - entropy_coefficient × Σ H(Normal_i)
```

Sは小さいほどよいスコアです。referenceよりよい候補ではAが負となり、その潜在値を出す確率が増える方向へ勾配降下します。悪い候補でもフィードバック更新します。referenceは最初の基準値評価で初期化し、更新後に`reference += reference_rate × (S-reference)`で移動平均を取ります。現在の候補をreferenceへ混ぜてからAを計算しません。

Adamの学習率は0.05、reference_rateは0.1、潜在entropyの係数は0.01、勾配norm上限は5、潜在平均の絶対値上限は6です。`adaptive`で変更できます。entropy項は潜在分布の広がりを保つ補助項で、丸め後のキャラ数値のentropyそのものではありません。スコア差のclipと平均・分散の制限を加えた実用的な推定であり、厳密な無偏推定や最適解の保証ではありません。

分布が変わっても、整数丸めで同じ候補が再び出ることはあります。再出現した候補も評価・更新します。サンプルを重複排除すると対数確率に対応する抽選分布が変わるためです。独立軸のモデルなので、キャラ間の相関や複雑な多峰性を直接モデル化しません。各回の改善や短い実験での収束も保証しません。

## 対戦条件と採用

両陣営の一度だけの出撃、1対1・1対3・3対1・3対3、一直線・1マス階段、90秒上限を維持します。敵は元アセットの別スナップショットで固定します。味方の探索値を同名の敵にも反映する設定ではありません。

trainだけが生成分布を更新します。予算内で実際に評価した候補からtrainスコアが最小のものを選び、validationの全体損失・全個別case・必要な全キャラ同時適用の非悪化を要求します。不採用なら設計基準値を維持します。holdoutは採否後の1回だけで、学習にも選び直しにも使いません。分布の中心は観察用で、未評価のまま推奨値として採用しません。Unityへの自動適用もありません。

## 実行と保存

単独リポジトリのルートで`python scripts/prepare.py --build-only`を実行後、LearningLabディレクトリで:

```powershell
python -m learning_lab.campaign --plan configs/defense_training_plan.json --template configs/defense_training_stages.json --output runs/defense-adaptive
python -m learning_lab.balance --config runs/defense-adaptive/experiment.json --settings runs/defense-adaptive/targets.json --run runs/defense-adaptive-search
```

| 保存物 | 内容 |
| --- | --- |
| manifest.json | 入力、search_strategy、解決済み学習設定、探索器・分布・数値範囲・編成生成コードのhash |
| trial-*.json | 候補、train成績、更新前後の平均・標準偏差・中心、advantage・損失・勾配norm |
| checkpoint.pt | 最新評価後の分布パラメータ、Adam状態、独立乱数器、reference、更新数、設定hash |
| report.json | 評価済み最良候補、採否、最終分布、更新数、validation・holdout |
| recommended-config.json | 採用条件に従った学習用実験設定 |

基準値のtrialだけはupdateがnullで、分布の勾配更新は0回です。checkpointはtensorと基本型だけで読み込み、状態復元後に同じ次候補・更新になることをテストします。**数値探索CLIの中断再開オプションは未実装**です。新しいrunを指定してください。PPO CLIのresumeとは別です。

旧JSONに`search_strategy`がなければ従来の`random-grid`として扱います。旧run・成績は書き換えず、現行の分布学習には新しくcampaignを生成します。0.4.0の小規模確認結果は[adaptive_search_20261009.json](results/adaptive_search_20261009.json)とリポジトリのREADMEに記録します。機能確認を全8体のバランス調整完了とは扱いません。
