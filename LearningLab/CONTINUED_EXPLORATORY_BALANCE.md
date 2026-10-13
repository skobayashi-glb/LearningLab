# 前回の分布を引き継ぐ1〜2時間の学習

この文書はGPT-6によって書かれています。

2026-10-13、LearningLab 0.7.1。ユーザー指定の1〜2時間を目安に、短縮版で完了した30種類・3候補・2更新の状態を引き継ぎます。分布の中心と広がり、Adamの更新状態、乱数、学習済みの参照損失を復元し、前回の基準評価と2候補の結果を再利用します。元のrunは変更せず、新しいrunへ分岐して続けます。

設定は`configs/continued_exploratory_training_plan.json`です。総候補数24のうち、3候補を継承し、21候補を追加で評価します。各キャラの分布は前回の2更新から累計23更新まで進みます。累計の学習候補をキャラごとに比較し、実測した最良候補を集めて最後に全30種類を同時検証します。ゲームへの性能値の自動適用はありません。

学習を継続するため、学習seed、対象中心・混合編成の重みと目標、64項目の探索範囲、学習率、変更罰則、設計者指定のコスト、敵の固定基準値、2種類の地形は前回と同じです。学習の相手中心種はアンモナイト・サメの2種類のままです。初期値から学び直す実験ではありません。候補生成に検証・最終確認の結果を入力しません。

検証と最終確認の相手・seedを増やします。検証はゴブリン・サメ・クロコダイル・深海シードラゴン、最終確認はアンモナイト・ヨシキリザメ・シーラカンス・シロナガスクジラの4種類ずつです。前回の検証・最終確認で使ったseedは再利用せず、全分割と分析seedの重複も禁止します。混合編成の相方には対応済み30種類を使います。

追加の学習対戦は21候補×480試合＝10,080試合です。基準値の検証、個別検証、全対象同時の検証、最終確認は各960試合で、合計3,840試合です。独立した5種類の編成分析は280試合で、新たに実行する予定は合計14,200試合です。前回の2,680試合が約15分21秒だったため、同程度の速度なら約81分を見込みます。端末負荷や戦闘の長さで変わるため、完了時刻は保証しません。

WebUIの候補数・更新回数は前回分を含み、対戦数と過去フレーム一覧は新しいrunで実行した分だけです。引き継いだ3候補・2更新を画面に明記します。以前の対戦フレームは元のrunに残します。最新戦闘はすべて表示し、過去フレームは4試合ごとに保存します。

```powershell
python -m learning_lab.campaign --plan configs/continued_exploratory_training_plan.json --template configs/cost_training_stages.json --output runs/continued-exploratory-campaign
python -m learning_lab.dashboard --config runs/continued-exploratory-campaign/experiment.json --settings runs/continued-exploratory-campaign/targets.json --run runs/continued-exploratory-search --continue-from runs/short-exploratory-search-20261013 --port 8767 --capture-every 40 --archive-every 4
```

`--continue-from`はadaptive-distribution方式の完了した候補checkpointを読み、必ず空の新規runへ書き出します。元のrunのconfig・settings・候補履歴・checkpoint・数値処理の互換性を検証します。戦闘の固定設定、学習条件、探索範囲、optimizer設定を変えた継続は拒否します。候補数の増加、plan識別情報、分析設定、検証と最終確認のseedだけを変更できます。試行の途中の戦闘からは再開せず、最後に保存した候補の続きから始めます。

学習相手が2種類という限界は残ります。今回の目的は更新回数を増やし、別の相手での全対象同時検証を広げて改善が続くかを確認することです。30種類の相手を網羅したバランス確定の実験ではありません。
