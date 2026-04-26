#!/bin/bash
# =============================================
# Novelove 自動デプロイスクリプト (v18.0.0 Git方式)
# 30分ごとにGitHubの最新コミットを確認し、
# 変更があれば git pull で全ファイルを自動更新する。
# =============================================

SCRIPTS_DIR="/home/kusanagi/scripts"
SHA_FILE="$SCRIPTS_DIR/.deploy_sha"
LOG_FILE="$SCRIPTS_DIR/deploy.log"
BRANCH="main"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# ログファイルが大きくなりすぎないよう100行に制限
if [ -f "$LOG_FILE" ] && [ "$(wc -l < "$LOG_FILE")" -gt 100 ]; then
    tail -50 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

cd "$SCRIPTS_DIR" || exit 1

# Gitリポジトリとして初期化されているか確認
if [ ! -d ".git" ]; then
    log "ERROR: .git ディレクトリが見つかりません。Gitリポジトリではありません。"
    exit 1
fi

# リモートの最新情報を取得
git fetch origin $BRANCH -q
if [ $? -ne 0 ]; then
    log "ERROR: git fetch 失敗（ネットワークエラー?）"
    exit 1
fi

LATEST_SHA=$(git rev-parse origin/$BRANCH)
LOCAL_SHA=$(git rev-parse HEAD)

if [ "$LATEST_SHA" = "$LOCAL_SHA" ]; then
    # 変更なし → 何もしない
    exit 0
fi

log "変更検知: $LOCAL_SHA -> $LATEST_SHA（Gitデプロイ開始）"

# デプロイ前に現在のコミットを記録（エラー時のロールバック用）
PREV_SHA=$LOCAL_SHA

# 強制同期（ローカルの変更は破棄されるが、本番環境は常にGitHubを正とする）
git reset --hard origin/$BRANCH -q
if [ $? -ne 0 ]; then
    log "ERROR: git reset --hard に失敗しました。"
    exit 1
fi

# 構文チェック（Pythonファイル）
SYNTAX_OK=true
for f in *.py; do
    if [ -f "$f" ]; then
        python3 -m py_compile "$f" 2>/dev/null
        if [ $? -ne 0 ]; then
            log "ERROR: $f の構文チェック失敗（ロールバック実行）"
            SYNTAX_OK=false
            break
        fi
    fi
done

if [ "$SYNTAX_OK" = false ]; then
    # 構文エラー → 前のコミットにロールバック
    git reset --hard $PREV_SHA -q
    log "ERROR: 構文エラーのため $PREV_SHA にロールバックしました。"
    exit 1
fi

# SHA更新（ログ用）
echo "$LATEST_SHA" > "$SHA_FILE"

log "OK: Git一括デプロイ完了 ($LATEST_SHA)"
