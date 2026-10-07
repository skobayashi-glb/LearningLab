# キャラ数値探索：両陣営を一度だけ出撃させる対戦評価

2026-10-07、LearningLab 0.3.0。キャラのパラメータ探索では`evaluation_mode=single-wave`を使います。敵AIやプレイヤー操作の学習は行いません。PPOによる操作学習は別の機能として残します。

## 1試行の流れ

1. 評価対象キャラ、人数パターン、ステージ、seedを決める。
2. 味方は評価対象を必ず1体含め、残りの味方と敵を候補プールから抽選する。
3. 両陣営の全個体を、シミュレーション時刻0で一度だけ出撃登録する。
4. 拠点の出撃口が空いた順に登場させる。その後は移動・攻撃・状態処理だけを進め、追加出撃しない。
5. 敵ボス拠点占領、味方メイン拠点破壊、または90秒の時間切れまでを1試行とする。

1対1でも、ユニットの死亡だけでは終了しません。生き残った個体は拠点へ進みます。両陣営の全個体が死亡して未決着なら時間切れになります。個体を倒した割合だけを勝敗とする評価ではありません。

| パターン | 味方 | 敵 |
| --- | --- | --- |
| 1v1 | 評価対象1体 | 抽選1体 |
| 1v3 | 評価対象1体 | 抽選3体 |
| 3v1 | 評価対象1体＋抽選2体 | 抽選1体 |
| 3v3 | 評価対象1体＋抽選2体 | 抽選3体 |

人数はplanの`encounters.patterns`で変更できます。ステージは一直線と、右1マス・上1マスを交互に進む階段状の2枚だけです。

## キャラをどう選ぶか

両campaignの`encounters.ally_pool`・`enemy_pool`は`supported_characters`を指定し、Exporterと能力監査に通る30種類をID順で候補にします。42種類中12種類は特殊移動・対象選択・位置効果・ノックバック・Sanded接触伝播が未対応で、`character-audit.json`と`targets.json`の`character_sampling.unsupported`に理由を記録します。能力を削って42種類を対象にしたことにはしません。

抽選は各候補から等確率、復元抽出です。同じキャラが複数体になる場合も、相手に同じキャラが出る場合もあります。評価対象を必ず含めることで、そのキャラが抽選されなかったために候補数値の良し悪しが分からない試行を防ぎます。

`active_characters`の8種類は数値を探索する対象です。抽選プールの30種類と同じ意味ではありません。対象以外の味方はplanの初期設計値、敵はUnityアセットから保存した元の基準値です。敵の同名キャラにも味方の候補値を適用することはありません。

相手を固定の4種類にしたい場合は、`encounters.enemy_pool`をIDの配列（例：`["shark", "amago", "ammonite", "blue-surgeonfish"]`）にします。敵数値を探索する設定ではありません。

case IDとseedのSHA-256から独立した乱数器を作ります。同じcase・seedなら、候補数値やステージ、戦闘結果にかかわらず同じ編成になります。各候補で同じ対戦条件を使い、比較時に抽選相手が変わることを防ぎます。各episodeの`encounter`へ実際の味方・敵ID列を保存します。

## 出撃と評価の扱い

両陣営の出撃登録は同時です。1マス幅の出撃口から全員が同時に重なって登場することはなく、空き待ちを含みます。味方は既定経路A、敵は味方メイン拠点へ向かいます。編成内の登録順・速度・接触・攻撃は戦闘結果に影響します。

Energyの残量・回復・出撃間隔・予告・敵の同キャラ3体制限は、この固定編成の抽選や人数を決めません。指定した人数を出すためEnergyを支払いません。敵の自動出撃は無効にし、味方の追加`deploy`も不合法にします。単発出撃登録後、評価器は`wait`だけで戦闘を進めます。生存キャラの補充・死亡個体の再出撃はありません。

`damage_per_energy`は互換性のため名前を維持し、「有効与ダメージ÷（対象キャラの登録個体数×候補のコスト）」として計算します。実際に消費したEnergyを測る指標ではありません。待機中の個体も登録数・生存率に含みます。ほかに生存率・味方拠点HP比率・拠点戦勝率を集計します。状態付与・支援の寄与を因果分解した指標ではありません。

既存の役割別目標帯は暫定的に引き継いでいます。繰り返し出撃から単発対戦への変更に合わせた目標帯の妥当性は、対戦結果を見て再調整する必要があります。全個別caseと同時適用validationの非悪化を採否条件にし、holdoutは選択後に一度だけ実行します。

## 実行

単独リポジトリのルートでBattleHostを準備します。以降は`LearningLab`ディレクトリで実行します。

```powershell
python scripts/prepare.py --build-only
cd LearningLab
python -m learning_lab.campaign --plan configs/defense_training_plan.json --template configs/defense_training_stages.json --output runs/defense-single-wave
python -m learning_lab.balance --config runs/defense-single-wave/experiment.json --settings runs/defense-single-wave/targets.json --run runs/defense-single-wave-search
```

通常campaignは8対象×4パターンで32caseです。防御planは各分割4seedなので、1候補・1分割あたり32case×2マップ×4seed＝256試合です。24候補のtrainは6,144試合、validation・holdoutは別に追加されます。この回数の本探索を実行済みという意味ではありません。

小さい`balance_targets.json`の直接実行例はshark/amagoの2種類を抽選プールに使います。広い30種類の対戦にはcampaignを生成してください。

旧実験JSON・ログ・checkpointは当時の履歴です。古い設定を読み込んでも自動で単発出撃へ変更しません。今回のC#ホスト変更でengine hashが変わるので、現行ソースから再exportし、新しいrunを作ります。候補をゲームへ自動適用しません。

## 現在の確認結果

[single_wave_20261007.json](results/single_wave_20261007.json)に、単独リポジトリでの73件のテストと小規模探索の要約を保存しています。2対象×4パターン、2候補、各分割1seedで、train32・validation32・holdout16、計80試合を確認しました。候補はvalidationで不採用となり、基準値を維持しました。全8対象の本探索や目標帯の確定、ゲームへの適用は実施していません。
