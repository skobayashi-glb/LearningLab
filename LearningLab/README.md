# LearningLab — 製品版から切り離せる学習基盤

このREADMEはGPT-6によって書かれています。

ゲーム本体と独立した、開発用のPPO学習・評価ツールです。PPO学習器はUnityをimportしません。独立C#戦闘ホストはゲームの数値計算ソースをリンクして利用します。製品版からこのフォルダ全体を外せます。

**戦闘学習は [ステージを使った戦闘学習](BATTLE.md) を参照してください。** 保存済みステージと実キャラを読み込むBattleHostを追加しました。ナビゲーションデモも基盤の検証用として残しています。

**キャラ数値だけを自動調整する場合は [BALANCE.md](BALANCE.md) を参照してください。** 操作AIを固定し、役割・コスト別の目標範囲に対してHP・攻撃力などを探索できます。

## 現在できること

- PyTorchによるPPO学習、合法行動マスク、複数シナリオの重み付き抽選。
- 学習用・検証用・最終確認用データの分離。同じステージfamilyの分割間重複を検出。
- 終端と時間打ち切りを区別したGAE。打ち切り時は最後の観測から価値を推定し、次エピソードの報酬は混ぜない。
- 方策・optimizer・乱数・環境状態・進捗の保存と再開。
- 固定方策との評価比較、行動履歴の記録、リプレイ再実行による状態照合。
- Python環境の差し替え、JSON通信で外部のC#等の環境へ接続。
- 製品側からの参照を検査する静的な境界チェック。

初版はCPU・単一環境・固定長観測・フィードフォワード方策です。BattleHostではカード選択・出撃・目的地変更を複数の離散判断で組み立てます。並列環境、履歴メモリ、本体との完全な戦闘一致は未実装です。キャラ数値探索はPPOとは別CLIとして実装しています。

## 製品版との境界

```text
開発時のみ
  ステージエディター / 保存済みアセットExporter
                     ↓ 版付きデータを書き出す
  LearningLab ──JSON── 独立したC#戦闘実行CLI
                     ↓ 比較・候補を確認して採用
製品側
  採用済みゲームデータ + 戦闘処理 + Unity表示
```

- 学習コード、Python、モデル、ログ、デバッグステージの一覧をゲームの実行時依存にしない。
- 将来のExporter/ImporterはEditor専用アセンブリから参照する。Runtimeから学習ツールへ逆参照しない。
- 共通戦闘コアを製品側でも使う場合、そのコアは製品に残る。学習器・評価器・プロセス通信はコアの外側に置く。
- バランス評価用AIの重みを製品に入れる必要はない。将来ゲーム内AIとして使う場合は、別の製品要件として推論部分を検討する。
- 採用数値は通常のゲームデータへ取り込む。実験中にAssetsを変更しない。
- デバッグ用ステージは本番StageCatalogやResources、Addressablesの配信グループに登録しない。将来のExporter実装時にも検査する。
- このツールを配布物へコピーしない。UnityのAssets外にあることに加え、独自配布スクリプトでも除外する。

## 起動

Python 3.10以上、NumPy、PyTorchが必要です。作業ディレクトリをこのREADMEのある場所へ移します。

```powershell
cd C:\Users\shoki\TowerDefense\LearningLab
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

以降の例の `python` は、必要に応じて `.\.venv\Scripts\python` に置き換えてください。既に依存があるPythonでも動作します。CUDAは必要ありません。

### 学習

```powershell
python -m learning_lab train --config configs/demo.json --run runs/demo --steps 16384
```

runディレクトリが空でない場合は上書きせず失敗します。別名のrunを使うか、保存済みの学習を再開します。

```powershell
python -m learning_lab train --config configs/demo.json --run runs/demo --steps 32768 --resume runs/demo/checkpoint.pt
```

`--steps` は追加数ではなく累計判断数です。configが変わった場合は再開を拒否します。厳密な再現は同一コード・環境・PyTorchで、同じrollout境界から再開する場合が対象です。rollout途中を異なる長さで区切るとPPO更新の区切りが変わります。

### 検証と比較

```powershell
python -m learning_lab evaluate --config configs/demo.json --checkpoint runs/demo/checkpoint.pt --split validation --output runs/demo/validation.json
python -m learning_lab evaluate --config configs/demo.json --baseline random --split validation --output runs/demo/random.json
python -m learning_lab replay --config configs/demo.json --report runs/demo/validation.json
```

結果JSONには平均報酬、終了種別の件数、平均判断数、打ち切り率、シナリオ別の生データとリプレイが入ります。方策評価はargmaxによる決定的行動です。デモの「success」は目的地到達を意味し、ゲームの勝率ではありません。

最終確認は `--split holdout` を明示した場合のみ実行されます。学習もチェックポイント選択もHoldoutを自動で参照しません。

### テストと切り離し検査

```powershell
python -m unittest discover -s tests -v
python check_release_boundary.py
```

境界検査はAssetsのRuntime C#/アセンブリ定義とPackagesの学習ツール参照、Assetsから学習フォルダへのシンボリックリンクを検査します。Unity player buildや配信アセット全体の検証の代替ではありません。ゲームの実行確認をしたという意味ではありません。

## 実ゲームを後から接続する

`learning_lab/contracts.py` の `LearningEnvironment` を実装し、configの `environment_factory` を `モジュール:クラス` に変更します。学習器はゲームクラスを直接知る必要がありません。

| API | 契約 |
|---|---|
| spec | 観測の要素数、行動名、schema ID。同じrunでは不変 |
| reset(scenario, seed) | 完全に初期化してObservationを返す |
| step(action) | 原子的に1命令を適用し、次判断時刻まで進める |
| save_state() | 環境乱数を含むJSON互換の完全状態 |
| load_state(state) | 状態を復元し、その時点の観測を返す |
| close() | 実行資源を解放 |

Observationはfloat32の一次元配列とbooleanの合法マスクです。最低1行動を合法にします。終端でも有効な形の観測を返し、step内で自動resetしないでください。

Transitionは報酬、terminated、truncated、infoを持ちます。終了時の `info.outcome` は必須です。同時に上限へ達した勝利はterminatedのみをtrueにします。環境エラーを敗北や待機へ変換しないでください。

ステージ、キャラ定義、ルールはconfigのシナリオparametersやenvironment_optionsから渡します。外部データを参照するadapterは、データを凍結しcontent hashをconfigへ含め、schema/実装版も管理してください。

BattleHostの出撃は選択操作を逐次行い、commitで確定します。選択操作の間は戦闘時間と報酬割引を進めません。

## 外部プロセスの接続

configのfactory/optionsを次のように変更すると、デモを別プロセスで実行できます。

```json
{
  "environment_factory": "learning_lab.rpc:JsonProcessEnvironment",
  "environment_options": {
    "command": ["python", "-m", "learning_lab.demo_server"],
    "timeout_seconds": 30
  }
}
```

実行するPythonは必要に応じて絶対パスを指定します。`command` はshell文字列ではなく引数配列。後からC# CLI等のコマンドへ変更できます。信頼できるローカルconfigのみを使用してください。

1要求・1応答のJSON Linesで、stdoutは通信専用、ログはstderrです。初版は1プロセス1環境・逐次要求で、StepBatchは未実装です。

```json
{"version":1,"id":1,"operation":"spec","payload":{}}
{"version":1,"id":1,"result":{"observation_size":3,"action_names":["wait","left","right"],"schema_id":"navigation-demo-v1"}}
```

操作は `spec / reset / step / save_state / load_state`。resetのpayloadはscenarioとseed、stepはaction、load_stateはstateを持ちます。Observationのwire形式はvaluesとaction_mask。stepのresultはobservation/reward/terminated/truncated/infoです。

異常応答は `{"version":1,"id":1,"error":"理由"}`。ID・版の不一致、プロセス終了、タイムアウトは実験異常にします。close時は子プロセスを終了して解放します。完全状態は保存・再現専用で、方策観測に混ぜません。

## ファイルと再現性

- `manifest.json`: config、hash、観測schema、ツール/実行環境版。
- `checkpoint.pt`: 各更新後に置換保存。tensorと基本型のみを使用し、weights_onlyで読み込む。
- `metrics.jsonl`: PPO損失と完了エピソード。更新番号で照合可能。
- 評価JSON: 生結果と行動ごとの状態hash。replayで一致検査。

クラッシュ直前のmetricsとcheckpointに差がある場合はcheckpointの更新番号を正とします。GPUや別OSでのビット一致は保証しません。

## 参照

- [PPO原論文](https://arxiv.org/abs/1707.06347)
- [PyTorchの確率分布](https://docs.pytorch.org/docs/stable/distributions.html)
- [Gymnasiumの時間打ち切り](https://gymnasium.farama.org/main/tutorials/handling_time_limits/)

本実装はGymnasiumと同じ終端/打ち切りの区別を採用しますが、Gymnasium自体は依存パッケージに含めていません。
