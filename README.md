# novel-writer

なろう・ラノベ系短編小説を週次で自動生成し、フィードバックを蓄積して品質を向上させる Python プロジェクト。

## 機能

- **小説自動生成**: Claude API による 2 段階生成（構成案 → 場面ごと本文）
- **LINE 通知**: 生成完了時にタイトル・ジャンル・テーマ・文字数・シリーズ情報を通知
- **シリーズ管理**: 設定・世界観・主人公が共通の作品群をシリーズとして管理
- **閲覧 Web アプリ**: ローカル FastAPI アプリで小説を読み、フィードバックを記録
- **読書進捗管理**: スクロール位置を自動保存し、続きから読める
- **知見蓄積**: フィードバックから Claude API が知見を抽出し、次回生成に反映

## セットアップ

### 必要環境

- Windows 11 + WSL2（Ubuntu）
- Python 3.12+
- Anthropic API キー
- LINE Messaging API チャンネルアクセストークン・ユーザー ID

### インストール

```bash
cd novel-writer
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 環境変数の設定

プロジェクトルートに `.env` ファイルを作成する。

```
ANTHROPIC_API_KEY=sk-ant-...
LINE_CHANNEL_ACCESS_TOKEN=...
LINE_USER_ID=...
```

### DB 初期化

DB は初回実行時に自動作成されます。手動で確認する場合：

```bash
source venv/bin/activate
python -c "import db; db.init_db(); print('DB初期化完了')"
```

## 使い方

### 小説を手動生成する

```bash
source venv/bin/activate

# ランダムジャンル・テーマで生成
python main.py --manual

# ジャンル・テーマを指定して生成
python main.py --manual --genre "異世界転生" --theme "勇者召喚からの逃走"

# シリーズの一作として生成（既存シリーズ名を指定すると追加、新規名は作成）
python main.py --manual --series "魔法少女クロニクル"
python main.py --manual --series "魔法少女クロニクル" --series-description "魔法少女たちの戦い"

# 登録済みシリーズ一覧を表示
python main.py --list-series
```

### Web アプリの画面一覧

| 画面 | URL | 機能 |
|---|---|---|
| トップ（作品一覧） | `/` | 未読・読書中・読了・フィードバック待ちをフィルタリング表示 |
| 作品読了画面 | `/novels/{id}` | 本文表示・スクロール進捗の自動保存・読了マーク |
| フィードバック | `/novels/{id}/feedback` | 評価（1〜5）・コメント入力・知見抽出の非同期実行 |
| シリーズ一覧 | `/series` | シリーズ別の話数・未読数を一覧表示 |
| シリーズ詳細 | `/series/{id}` | シリーズ内の話数一覧 |

### フィードバックの流れ

```
本文を読む → 読了マーク → フィードバック入力（評価・コメント）
    → Claude API が知見を抽出 → DB に保存
    → 次回の小説生成プロンプトに自動反映
```

### 閲覧 Web アプリを起動する

```bash
source venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 8000
```

ブラウザで `http://localhost:8000` を開く。

---

## Windows からの利用（バッチファイル）

プロジェクトルートに 2 種類のバッチファイルが用意されている。

### 小説を読む.bat — 閲覧アプリ起動

ダブルクリックするだけで FastAPI サーバーが起動し、ブラウザが自動で開く。

> **注意**: エクスプローラーから `\\wsl$\Ubuntu\...` を直接開いて実行するのではなく、
> 以下の手順でデスクトップショートカットを作成してから使用すること。

**デスクトップショートカットの作成手順:**

1. デスクトップを右クリック →「新規作成」→「ショートカット」
2. 場所に以下を入力して「次へ」
   ```
   \\wsl$\Ubuntu\home\（ユーザー名）\novel-writer\小説を読む.bat
   ```
3. 名前を「小説を読む」として「完了」

サーバーを停止するには「novel-reader サーバー」ウィンドウを閉じる。

### 小説生成.bat — Windowsタスクスケジューラ登録

週次で小説を自動生成するための設定手順。

#### タスクスケジューラへの登録

1. スタートメニューで「タスクスケジューラ」を検索して開く
2. 右ペインの「基本タスクの作成」をクリック
3. 以下の設定で作成する

| 項目 | 設定値 |
|---|---|
| 名前 | novel-writer 自動生成 |
| トリガー | 毎週（例: 日曜日 09:00） |
| 操作 | プログラムの開始 |
| プログラム | `\\wsl$\Ubuntu\home\（ユーザー名）\novel-writer\小説生成.bat` |
| 開始 | （空欄でよい） |

4.「完了」をクリックして登録

#### 実行ログの確認

```bash
# プロジェクトルートで実行
cat logs/scheduler.log
```

---

## ディレクトリ構成

```
novel-writer/
├── app.py              # FastAPI Webアプリ
├── main.py             # 実行エントリーポイント
├── generator.py        # 小説生成（Claude API 2段階生成）
├── notifier.py         # LINE通知
├── knowledge.py        # 知見抽出・管理
├── db.py               # DB操作（SQLite）
├── settings/
│   ├── base_prompt.md  # 生成プロンプトテンプレート
│   ├── model_config.json
│   └── genre_config.json
├── templates/          # Jinja2 HTMLテンプレート
├── data/               # SQLiteデータベース（自動作成）
├── logs/               # 実行ログ
├── exports/            # エクスポートファイル
├── 小説生成.bat        # タスクスケジューラ用
└── 小説を読む.bat      # デスクトップショートカット用
```

## テスト

```bash
source venv/bin/activate
python -m pytest
```

## 将来の拡張予定

- **長編シリーズ対応** — 短編で蓄積したノウハウを活かした連載形式への展開
- **カクヨム・小説家になろう 自動投稿** — 生成した小説の外部サイトへの投稿自動化
- **知見抽出エラー監視** — バックグラウンド処理の失敗を LINE で通知
