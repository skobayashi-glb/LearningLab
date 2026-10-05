# ステージを使った戦闘学習

Unityを起動せず、保存済みのStageDefinition・味方キャラ・モーション・状態定義を読み込んで学習する開発用環境です。PythonのPPOと独立したC#戦闘ホストをJSONで接続します。ゲームRuntimeへの依存追加はありません。

## 実行

Python 3.10以降と.NET 10 SDKを使います。リポジトリ直下から次を実行してください。書き出し先と新規run名は毎回変えます。

```powershell
cd LearningLab
python -m pip install -r requirements.txt
dotnet build BattleHost/BattleHost.csproj -c Release
python -m learning_lab.export_battle --setup configs/battle_setup.json --output runs/my-battle.json
python -m learning_lab train --config runs/my-battle.json --run runs/my-training --steps 8192
python -m learning_lab evaluate --config runs/my-battle.json --checkpoint runs/my-training/checkpoint.pt --output runs/my-evaluation.json
python -m learning_lab replay --config runs/my-battle.json --report runs/my-evaluation.json
```

再開は同じconfig・runに `--resume runs/my-training/checkpoint.pt` を指定し、`--steps` に再開後の累計判断数を渡します。短い学習は接続確認であり、十分に強いAIや調整済みのゲームが得られることを意味しません。

## ステージの準備

ステージエディターで次のStageDefinitionを開き、地形・味方拠点・敵拠点を編集して保存できます。

| Asset（Assets/Editor/LearningDebugStages/） | 用途 |
|---|---|
| RL_OpenField.asset | 開けた地形・学習用 |
| RL_ChokePoint.asset | 通路を絞った地形・検証用 |
| RL_Detour.asset | 迂回が必要な地形・最終確認用 |

自作マップも `stage_asset` を差し替えて利用できます。開発専用マップはEditorフォルダー内へ保存してください。ステージ保存処理はEditor内のマップを製品用StageCatalogに登録しません。マップのコピーや近い派生版は同じ `family` にして、trainとvalidation/holdoutの間で混ぜないでください。

`configs/battle_setup.json` の各scenarioで以下を指定します。

- `main_base`: HP、占有セル、出撃口。footprint/spawn_slotsは拠点原点からの相対座標 `{ "x": 1, "y": 0 }`。配置のfacingで回転します。プレハブの値を推測しません。
- `roster`: 味方の所有カード。modelNameの配列で、重複は別カードです。初期手札を作り、出撃中のカードは手札から除外し、死亡時に戻します。
- `enemy_pool`: 敵として使う味方キャラの候補。例ではshark/amago。敵用ステータスは書き出し時の別スナップショットで固定します。
- `rules`: 初期Energy、回復間隔、出撃予告、重複上限、時間制限など。省略値はBattleHost/Data.csのRulesに集約。未知のキーはエラーです。
- `enemy_bases`: 配列index文字列をキーにHPや `garrison` を上書きできます。garrisonはmodelName配列。未指定は空で、本体の自動守備隊抽選は使いません。
- `initial_units`: 必要なら開始時の配置を指定。`team`（味方0/敵1）、`character`、絶対座標 `cell` / `destination`。味方はroster内のカードを消費します。
- `boss_base_index`: 勝利対象の敵拠点。省略時は唯一のMain型敵拠点です。このホストの勝利条件はボス拠点占領、敗北条件は味方メイン拠点HPゼロです。StageDefinitionの複合勝利条件は取り込みません。

すべての占有セル・出撃口をマップ内に置き、出撃口を通行可能にしてください。観測サイズは `envelope` で実験全体に固定します。地形・拠点数が上限を超えると書き出し検証に失敗します。

## 出撃と戦闘

味方AIはカードを順番に選び、出撃元と目的地を指定してcommitします。既存部隊の目的地変更、カード破棄、待機も可能です。出撃にはEnergy・手札・部隊数・全体ユニット数・到達可能な経路の検査があります。出撃口が埋まっている間は待機し、空くと順に出現します。

敵は一定間隔で、残Energyの設定割合を予算としてenemy_poolから抽選します。コスト、同キャラ上限、部隊数、到達可能な出撃元/目的地を制約にし、予告後に出撃します。予告中に拠点を失った場合は出撃を取り消します。支払い済みEnergyは戻しません。乱数seedを固定すると同じ判断列で再現できます。

移動・旋回・索敵・攻撃準備・命中・回復時間・ダメージ・死亡・拠点占領を固定tickで進めます。通常拠点はHPゼロで陣営が反転しHPを回復、味方メイン拠点は破壊で敗北です。

選択操作は戦闘時間を進めません。commit/discard/waitで `decision_seconds`（既定0.5秒）進みます。無時間操作の連続数には上限があります。PPOの割引にも経過時間0/1を渡すため、カード選択数だけで将来報酬が減りません。

観測は手札の性能、選択内容、拠点HP/陣営、ユニット位置/向き/攻撃準備/状態残時間、地形、部隊目的地、敵出撃予告の位置・残時間・人数です。敵の乱数状態や将来の抽選結果は渡しません。リカレント方策ではないため、部分観測への対応には限界があります。

## 調整する値と結果

`ally_overrides` でキャラごとに `hp / power / speed / cost / defense` を変更できます。

```json
"ally_overrides": { "shark": { "hp": 66, "power": 13, "speed": 1.7, "cost": 3 } }
```

味方だけに適用し、enemy_baselineは変更しません。元のUnityアセットも変更しません。別の候補は別configへ書き出し、同じステージ・評価seedで比較してください。PPOが更新するのはプレイ方策です。パラメータの自動探索は別CLIの [BALANCE.md](BALANCE.md) を参照してください。Unityへの書き戻しは未実装です。

評価JSONの各episodeのinfo.metricsに、陣営・キャラ別の出撃数、死亡数、与ダメージ、生存時間、行動不能時間を記録します。単独の勝率やダメージで強さを断定せず、コスト、編成相乗効果、地形、出撃機会を揃えて比較します。出撃数には出撃口で待機中の個体も含みます。

書き出しは元アセットのhashとBattleHostのhashを保存します。ホスト変更後は再書き出しが必要です。save_stateはseed・scenario・全判断履歴を保存し、load_stateは再実行で隠れ状態も復元します。高速な定数時間snapshotではありません。異なるOS/ランタイム間のビット一致は未保証です。

## 現時点の適用範囲

BodyBox・AttackShape・AttackAim・EngagementRange・状態寿命・状態反応解決は、ゲーム側C#ソースをリンクしてコンパイルします。UnityEngine互換型は数値演算と属性だけで、Unity物理を実行しません。

ただし、**戦闘全体がUnity本体と同一になったわけではありません**。移動、接触回避、隊列、索敵の切り替え、出撃スケジューラー、守備隊の行動は独立実装です。守備隊は指定した初期配置として扱い、占領後の自動補充はありません。マップのギミック・アイテム・支援役も含みません。

飛行、nearest以外の対象選択、回復/バフ役、位置効果、HPしきい値ノックバック、Sanded接触伝播、確率的な状態付与は未対応として拒否します。複数の攻撃演出イベントは最初の時刻を命中時刻に使い、多段ダメージを自動生成しません。実製品の調整値を採用する前に、本体と同じ初期配置・命令による軌跡と戦闘結果の照合が必要です。

## 検証と製品版からの除外

```powershell
python -m unittest discover -s tests -v
python check_release_boundary.py
```

BattleHostのビルド後にテストしてください。ホスト未ビルド時は戦闘テストをskipします。静的境界検査はRuntime/PackagesからのLearningLab参照、StageCatalogからのEditor専用マップ参照を検出します。Unity Playerのビルド確認を代替するものではありません。

製品配布時はLearningLab全体を除外できます。サンプルステージはAssets/Editor内にあり製品用カタログへ登録しません。Python、.NET CLI、学習モデル、実験ログをゲームの実行要件にしません。
