# キャラのパラメータだけを自動調整する

現行の数値探索は、**両陣営を一度だけ出撃させる固定対戦**です。`evaluation_mode=single-wave`で1対1・1対3・3対1・3対3を評価し、追加出撃や敵AIの出撃判断を使いません。[抽選・出撃・評価の詳細](SINGLE_WAVE.md)。キャラの候補値を戦闘シミュレーションで比較し、役割・コストごとに指定した目標範囲へ近づけます。0.4.0から、数値範囲内の候補を生成し、候補1件のtrain評価後に生成分布の平均・広がりをREINFORCE＋Adamで勾配更新します。次の候補は更新後の分布から生成します。[分布モデル・損失・更新順序](ADAPTIVE_SEARCH.md)。大域的な最適解は保証しません。旧実験は当時の出撃方式での履歴であり、新しいcampaignとrunで評価します。

## 実行

戦闘フレームとパラメータ更新を確認しながら実行する場合は、[学習モニター](MONITORING.md)を使います。通常の数値探索と同じ評価・分布更新を行い、ローカルWebUIへ実座標・HP・候補と分布の履歴を表示します。起動中の一時停止・再開に対応します。

先に [BATTLE.md](BATTLE.md) の手順でBattleHostをビルドし、実験configを出力してください。LearningLabディレクトリで実行します。

```powershell
python -m learning_lab.balance --config runs/my-battle.json --settings configs/balance_targets.json --run runs/my-balance
```

学習済みPPOモデルは不要です。サンプルはサメ・アマゴのHPと攻撃力を探索し、速度・出撃コスト・敵ステータスを固定します。攻撃周期・射程・役割・敵の行動も変更しません。既存のrunへの上書きは拒否します。分布・Adam・乱数器をcheckpointへ保存しますが、数値探索CLIの中断再開オプションは未実装です。完了した候補と更新の記録は残ります。

**現行ステージ（2026-10-07）:** 両setupは一直線・1マス階段の2枚だけを全分割で使います。planのtrain/validation/holdout seedは重複を禁止します。評価は固定地形での対戦編成の抽選に対する確認であり、未見地形への汎化を示しません。各分割の試合数は2マップ×seed数×case数です。

## 役割・コストと評価目標

**強弱の割り当てはユーザーが [strength_assignments.json](configs/strength_assignments.json) で変更できます。** `weak`（弱め）、`standard`（標準）、`strong`（強め）、`conditional`（編成依存）のいずれかを指定します。例として `"shark": "strong"` を `"shark": "weak"` に変更できます。全42体のキーを残してください。

Unity Editorでも設定できます。`Tools > LearningLab > 強弱設定`を開くか、`Tools > Characters > Balance Editor`の「学習の強弱設定」ボタンを使います。全42キャラから弱め・標準・強め・編成依存を選び、「保存」を押すと同じ`strength_assignments.json`へ保存します。キャラ名・IDで検索できます。

ゲームだけをcloneした場合は「JSONを選択」でLearningLabリポジトリ内の`LearningLab/configs/strength_assignments.json`を選びます。保存先を画面に表示し、そのプロジェクトで選んだ場所を覚えます。保存後はcampaignを再生成してください。既存runの凍結設定や、ゲーム本体のHP・攻撃力を直接変更する操作ではありません。

自動探索はこのファイルを書き換えず、指定を評価目標として固定します。HPやコスト、役割、技を強弱ラベルから勝手に変換することもありません。変更後はcampaignを新しいディレクトリへ再出力してください。別ファイルを使う場合は `--strengths パス` を指定できます。既存runには影響しません。

防御力は [防御仕様](../Docs/defense_damage_spec.md) に従います。profileの `defense` は固定値、`defense_range` を付けた場合だけ探索対象です。通常は0〜100、壁役は0〜200の整数を生成します。防御0も有効な候補です。防御の変更量罰則は基準値と100の大きい方で正規化し、基準0でのゼロ除算を避けます。

キャラごとの個性と意図的な強弱を含む設定は `configs/character_balance_plan.json` に追加しました。42体の設計案と、最初に探索する8体を分けています。キャラ別の説明は [設計表](../Docs/rl_character_balance_plan.md)、初回結果は [実験記録](../Docs/rl_character_balance_first_run.md) を参照してください。

```powershell
python -m learning_lab.campaign --plan configs/character_balance_plan.json --template configs/battle_setup.json --output runs/character-campaign
python -m learning_lab.balance --config runs/character-campaign/experiment.json --settings runs/character-campaign/targets.json --run runs/character-search
```

campaignは初期HP・攻撃力・速度・コストを実験用に設定し、敵の元データを固定します。役割・強弱別の目標と、HP/攻撃力の探索範囲（初期値の50〜150%）を生成します。壁役の攻撃力は固定です。`freeze_others`は他の味方を初期値に固定します。各対象を必ず1体含め、残りの味方・敵を対応済み30種類から等確率の復元抽出で選びます。未対応12種類は理由を監査に残します。8対象×4人数パターンで32caseを作り、Energy残量に関係なく指定した人数だけ時刻0に登録します。

個性は既存の技・状態・射程・攻撃周期と固定速度で維持します。新しい技を作る仕組みや、プレイ上の個性の重複を自動判定する仕組みではありません。初回の目標帯は仮説であり、強弱の割り当てが達成されたかは結果で確認します。

**サンプルの目標値は動作確認用の仮設定です。ゲームとしての適正値が確定したものではありません。** 役割の名前だけから適切な強さを推定する仕組みではなく、各評価caseに目標を明示します。

| 設定 | 意味 |
|---|---|
| parameters | 調整するキャラ・項目・min/max。基準値が範囲内にあることを要求する |
| search_strategy | 現行はadaptive-distribution。省略した旧設定はrandom-grid |
| adaptive | 学習率・分布の初期幅・幅の上下限・entropy係数・referenceの更新率など |
| candidate_budget | 基準値を含む評価候補数。基準値以外を評価するたびに分布を更新する。再出現も許可する |
| cases[].character / role | 評価対象のキャラと設計上の役割 |
| reference_cost | 実験開始時のコスト。元データと一致しないとエラー |
| roster | 評価対象を含む固定パーティ。単発対戦では抽選した味方の種類へ設定する |
| evaluation_mode / cases[].encounter | 単発出撃方式、両陣営の人数・抽選プール |
| targets | 指標ごとのmin/max、評価尺度scale、重要度weight |
| change_weight | 基準値からの変更量に対する罰則。不要な変更を抑える |
| seeds | train/validation/holdoutの固定seed。分割間の重複を禁止 |

更新対象は `hp / power / speed / cost / defense` から選べます。HP・攻撃力・コストは正の整数、防御力は0以上の整数です。防御力の探索は明示した場合のみ行います。コストを探索対象に加えても目標帯は自動で変更しません。コスト帯を変える調整は別の実験設定にしてください。

利用できる指標は次の4つです。

- `damage_per_energy`: 評価対象キャラの有効与ダメージ ÷（出撃登録数×コスト）。単発対戦ではEnergyを消費せず、コストで正規化した指標として使います。
- `survival_fraction`: 評価対象キャラの出撃個体のうち、終了時に死亡していない割合。
- `main_hp_ratio`: 終了時の味方メイン拠点HP比率。防衛への貢献を見る補助指標。
- `win_rate`: 指定編成でのステージ勝率。時間切れは勝利に含めない。

各指標をcase内でステージweightに従って平均し、目標範囲外の距離をscaleで割って二乗します。指標weight、caseのweightで平均し、探索時は変更量の罰則も加えます。範囲内の値に対する罰則はゼロなので、無制限に強化する目的関数ではありません。

例えば、アタッカーではコスト当たりダメージと勝率、タンクでは生存率・拠点HPを目標にできます。ただし、生存率だけでは後方に滞留するキャラも高評価になります。混成編成や異なる地形を追加し、複数指標で判定してください。支援役は戦闘ホスト自体が未対応です。

サンプルの単一キャラ編成は基礎性能の比較用で、相乗効果の検証ではありません。同じcharacterのcaseを複数用意し、異なる混成rosterを指定できます。目標キャラが一度も出撃しないcaseは実験エラーにします。初期配置・守備隊ありのcaseは、出撃コストの分母が不正確になるため初版では拒否します。出撃口で待機中の個体も出撃数に含まれる点に注意してください。

## 固定された操作方法

`one-opening-wave-per-team-then-wait-v1`で両陣営を一度だけ登録し、出撃口が空いた順に登場させます。味方の追加出撃を禁止し、敵AIを無効にします。味方は既定経路A、敵は味方拠点へ進み、評価器は終了まで`wait`だけを送ります。同じcase・seedでは全候補と両ステージに同じ編成を使います。登録順・速度・接触で実際の登場時刻や戦闘結果は変わります。敵の基準値は候補間で固定します。

旧settingsの`evaluation_mode`未指定時は、再現性のため従来の巡回出撃を維持します。現行の同梱設定はすべて単発出撃を明示します。既存の役割別目標帯は暫定的に引き継いでおり、単発対戦に適した目標かを再検証してください。

この操作方法での性能評価に偏るため、人間や他の戦略でも適正になるとは限りません。方策学習と数値調整を同時に動かさないことで、最初の比較条件を揃えています。

## 候補の選択と出力

1. trainだけで基準値と候補を比較し、最小スコアの候補を選ぶ。同点は先に評価した候補を優先する。
2. 選択した候補と基準値をvalidationで比較する。全体または個別caseの損失が悪化したら、基準値を維持する。
3. 他キャラを固定したcaseがある場合、候補を全キャラへ同時適用してvalidationを再評価する。全体または個別caseが悪化したら基準値を維持する。
4. 最終候補を同時適用した状態でholdoutを一度評価し、結果を報告する。holdoutを使って別候補を選び直さない。

出力は `manifest.json`（入力・固定方策）、`trial-*.json`（候補別の指標と損失）、`report.json`（比較と推奨値）、`recommended-config.json`（推奨値適用済みの実験データ）です。敵の基準データは変更しません。Unityアセットへの自動書き戻しも行いません。

`passed_validation_gate: false` の場合は改善候補なし、または検証で悪化したため、推奨値が現行値のままになります。これは探索処理の失敗を意味しません。試行回数・seed数・ステージ数が少ない結果には統計的な確証がなく、勝率帯を精密に判定するには試行を増やす必要があります。サンプルの各2seedは接続確認用です。

本体との移動・戦闘一致は未検証です。推奨値を製品へ採用する前にUnity本体で照合してください。LearningLab全体を製品から除外できる構成は維持します。
