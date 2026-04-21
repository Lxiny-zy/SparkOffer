#!/bin/bash
# 一键清除所有面试数据，用于调试

DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$DIR/data"
USERS_DIR="$DATA_DIR/users"

read -p "⚠ 将清除所有用户的面试记录、向量记忆和个人画像，是否继续？[y/N] " confirm
[[ "$confirm" =~ ^[yY]$ ]] || { echo "已取消"; exit 0; }

# 清空数据库（sessions + 向量记忆）
sqlite3 "$DATA_DIR/interviews.db" "DELETE FROM memory_vectors; DELETE FROM sessions;" 2>/dev/null

# 清空所有用户的画像数据
if [ -d "$USERS_DIR" ]; then
    for user_dir in "$USERS_DIR"/*/; do
        [ -d "$user_dir" ] || continue
        # 重置画像
        profile="$user_dir/profile/profile.json"
        [ -f "$profile" ] && echo '{}' > "$profile"
        # 清除 insights
        rm -f "$user_dir/profile/insights/"*.md 2>/dev/null
    done
fi

# 兼容旧的单用户路径
[ -f "$DATA_DIR/user_profile/profile.json" ] && echo '{}' > "$DATA_DIR/user_profile/profile.json"
rm -f "$DATA_DIR/user_profile/insights/"*.md 2>/dev/null

echo "已清除全部数据"
