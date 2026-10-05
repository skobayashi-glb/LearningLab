# LearningLab

現在の制作環境から分離した非公開リポジトリです。このリポジトリだけのcloneで、
編集・サンプル実行に必要なコードとデータが揃います。他のリポジトリの配置は不要です。

## 起動

Python 3.10以降を使用します。ブラウザツールにはNode.js 24以降、
MotionStudio・EffectStudio・LearningLabには.NET 10 SDKが必要です。
Python依存は仮想環境にインストールすることを推奨します。
VoxelStudioのFBX出力にはBlenderも必要です。

```powershell
python -m pip install -r requirements.txt
python scripts/prepare.py
python scripts/check.py
cd LearningLab
python -m learning_lab train --config configs/demo.json --run runs/demo --steps 16384
```

## 構成と共有コード

既存の相対パスとファイル形式を維持するため、ツール本体は同名フォルダに置いています。
Assetsや他ツール名のフォルダには、このツールが必要とする同梱データ・共有コードがあります。
独立したGitリポジトリへの参照やシンボリックリンクではありません。
`repository.json` が起動設定、`SOURCE_MANIFEST.json` が取り込み元パスとSHA-256です。
共有コードを更新する際は、元の変更を確認して必要なファイルだけ取り込み、テストしてください。
同梱スナップショットを別リポジトリへ自動的に書き戻すことはありません。

## Unityとの連携

通常の編集・学習では同梱データを使います。Unity側へのコピーは明示的に行います。

```powershell
python scripts/export_unity.py --project C:/path/to/game --dry-run
python scripts/export_unity.py --project C:/path/to/game
```

既存のUnity .metaを保持し、同梱の保護済みモーションや元アセットを変更しません。
LearningLabの実験結果・調整候補は自動採用しません。
VoxelStudio・UIStudio・MotionStudioの直接出力先は、必要に応じて
`UNITY_PROJECT_ROOT` 環境変数でゲームのルートを指定できます。

## 検証と生成物

`python scripts/check.py` で同梱ソースの存在、C#参照、編集元データを確認できます。
エフェクトのbake出力は `python scripts/prepare.py` で生成します。
プレビュー・bake出力・ビルド成果物・大量の学習ログはGitに保存しません。
モデル・モーション・画像・定義・学習設定・結果の要約は保存します。
公開ライセンスは付与していません。第三者素材の権利表記は元のファイルに従います。
