#!/bin/bash
# =============================================
# Novelove 自動デプロイスクリプト
# 30分ごとにGitHubの最新コミットを確認し、
# 変更があればPythonファイルを自動更新する。
# =============================================

SCRIPTS_DIR="/home/kusanagi/scripts"
SHA_FILE="$SCRIPTS_DIR/.deploy_sha"
LOG_FILE="$SCRIPTS_DIR/deploy.log"
REPO="tmy0228-design/novelove-auto-post"
BRANCH="main"
TARGET_FILES="auto_post.py novelove_core.py novelove_fetcher.py novelove_soul.py novelove_ranking.py novelove_writer.py"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# ログファイルが大きくなりすぎないよう100行に制限
if [ -f "$LOG_FILE" ] && [ "$(wc -l < "$LOG_FILE")" -gt 100 ]; then
    tail -50 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

# GitHubから最新コミットSHAを取得
LATEST_SHA=$(curl -sf "https://api.github.com/repos/$REPO/commits/$BRANCH" | python3 -c "import sys,json; print(json.load(sys.stdin)['sha'][:7])" 2>/dev/null)

if [ -z "$LATEST_SHA" ]; then
    log "ERROR: GitHub APIからSHA取得失敗（ネットワークエラー?）"
    exit 1
fi

# ローカルSHAと比較
LOCAL_SHA=""
if [ -f "$SHA_FILE" ]; then
    LOCAL_SHA=$(cat "$SHA_FILE")
fi

if [ "$LATEST_SHA" = "$LOCAL_SHA" ]; then
    # 変更なし → 何もしない（ログも出さない。静かに終了）
    exit 0
fi

log "変更検知: $LOCAL_SHA -> $LATEST_SHA（デプロイ開始）"

# バックアップ作成
BACKUP_DIR="$SCRIPTS_DIR/.deploy_backup"
mkdir -p "$BACKUP_DIR"
for f in $TARGET_FILES; do
    if [ -f "$SCRIPTS_DIR/$f" ]; then
        cp "$SCRIPTS_DIR/$f" "$BACKUP_DIR/$f.bak"
    fi
done

# GitHubからダウンロード
DOWNLOAD_OK=true
for f in $TARGET_FILES; do
    curl -sf -H "Cache-Control: no-cache" \
        "https://raw.githubusercontent.com/$REPO/$BRANCH/$f" \
        -o "$SCRIPTS_DIR/$f.new"
    if [ $? -ne 0 ]; then
        log "ERROR: $f のダウンロード失敗"
        DOWNLOAD_OK=false
        break
    fi
done

if [ "$DOWNLOAD_OK" = false ]; then
    # ダウンロード失敗 → バックアップから復元
    for f in $TARGET_FILES; do
        if [ -f "$BACKUP_DIR/$f.bak" ]; then
            cp "$BACKUP_DIR/$f.bak" "$SCRIPTS_DIR/$f"
        fi
        rm -f "$SCRIPTS_DIR/$f.new"
    done
    log "ERROR: ダウンロード失敗のためロールバック実行"
    exit 1
fi

# 構文チェック（全ファイル通ったら反映）
SYNTAX_OK=true
for f in $TARGET_FILES; do
    python3 -m py_compile "$SCRIPTS_DIR/$f.new" 2>/dev/null
    if [ $? -ne 0 ]; then
        log "ERROR: $f の構文チェック失敗（デプロイ中止）"
        SYNTAX_OK=false
        break
    fi
done

if [ "$SYNTAX_OK" = false ]; then
    # 構文エラー → バックアップから復元
    for f in $TARGET_FILES; do
        if [ -f "$BACKUP_DIR/$f.bak" ]; then
            cp "$BACKUP_DIR/$f.bak" "$SCRIPTS_DIR/$f"
        fi
        rm -f "$SCRIPTS_DIR/$f.new"
    done
    log "ERROR: 構文エラーのためロールバック実行"
    exit 1
fi

# 全チェックOK → 本番反映
for f in $TARGET_FILES; do
    mv "$SCRIPTS_DIR/$f.new" "$SCRIPTS_DIR/$f"
done

# SHA更新
echo "$LATEST_SHA" > "$SHA_FILE"

log "OK: デプロイ完了 ($LATEST_SHA) - $TARGET_FILES"
