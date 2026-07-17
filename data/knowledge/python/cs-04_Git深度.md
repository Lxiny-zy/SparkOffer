# Git 深度

## 一、核心概念

### 四大区域
```
Working Directory (工作区)
    ↓ git add
Staging Area / Index (暂存区)
    ↓ git commit
Local Repository (本地仓库)
    ↓ git push
Remote Repository (远程仓库)
```

### 对象模型
Git 是**内容寻址文件系统**：
- **Blob**：文件内容
- **Tree**：目录结构
- **Commit**：快照 + 元信息（parent、author、message）
- **Tag**：指向 commit 的标签

每个对象用 SHA-1 哈希标识。

### 引用
- **分支**：指向某 commit 的可移动指针
- **HEAD**：当前分支指针
- **Tag**：指向 commit 的固定指针
- **远程分支**：`origin/main` 等

---

## 二、常用命令

### 初始化与克隆
```bash
git init
git clone https://github.com/user/repo.git
git clone --depth 1 ...  # 浅克隆（只要最新）
```

### 配置
```bash
git config --global user.name "Alice"
git config --global user.email "alice@example.com"
git config --global core.editor "code --wait"
git config --global pull.rebase true  # pull 时用 rebase 而非 merge
git config --list
```

### 状态与差异
```bash
git status
git status -s      # 简洁
git diff           # 工作区 vs 暂存
git diff --cached  # 暂存 vs 仓库
git diff HEAD      # 工作区 + 暂存 vs 仓库
git diff main..feature  # 分支差异
git diff --stat    # 只看统计
```

### 添加与提交
```bash
git add file.txt
git add .          # 所有改动
git add -p         # 交互式（逐 hunk 选择）
git add -u         # 只添加已跟踪的改动

git commit -m "msg"
git commit -am "msg"  # 跳过 add 直接 commit 已跟踪文件
git commit --amend    # 修改最后一次 commit
git commit --fixup <sha>  # 生成 fixup commit（配合 rebase 自动合并）
```

### 查看历史
```bash
git log
git log --oneline --graph --all
git log -p file.txt      # 文件的变更历史
git log --author="Alice"
git log --since="2 weeks ago"
git log -S "func_name"   # 找引入/删除某字符串的 commit
git log --grep="fix"     # 提交信息搜索

git show <sha>
git blame file.txt       # 每行作者
```

### 分支
```bash
git branch                # 列出本地分支
git branch -a             # 含远程
git branch feature        # 创建
git switch feature        # 切换（2.23+ 推荐）
git switch -c feature     # 创建并切换
git checkout feature      # 旧命令，切换 or 恢复文件（歧义）

git branch -d feature     # 删除（未合并拒绝）
git branch -D feature     # 强制删除
git branch -m new-name    # 重命名当前分支
git push -d origin feature  # 删除远程分支
```

### 合并
```bash
git merge feature
git merge --no-ff feature    # 强制产生合并 commit（保留分支历史）
git merge --squash feature   # 压缩为一个 commit
git merge --abort            # 冲突时取消
```

### Rebase
```bash
git rebase main              # 把当前分支"变基"到 main
git rebase -i HEAD~3         # 交互式（pick/squash/edit/drop）
git rebase --continue        # 解决冲突后继续
git rebase --abort

git rebase --onto main feature~5 feature  # 高级：截取一段
```

### 远程
```bash
git remote -v
git remote add upstream https://...
git fetch origin
git pull                     # = fetch + merge（或 rebase）
git pull --rebase

git push origin feature
git push -u origin feature   # 关联上游
git push --force-with-lease  # 安全的强推（检查远程是否被他人更新）
```

---

## 三、撤销与回退

### 工作区撤销
```bash
git restore file.txt                  # 丢弃工作区改动（2.23+）
git checkout -- file.txt              # 旧版
```

### 暂存区撤销
```bash
git restore --staged file.txt         # 取消暂存
git reset HEAD file.txt               # 旧版
```

### 回退 commit

**reset**（改历史）：
```bash
git reset --soft HEAD~1    # 仅回退 commit，保留暂存和工作区
git reset --mixed HEAD~1   # 回退 commit 和暂存（默认）
git reset --hard HEAD~1    # 全部回退（危险）
```

**revert**（新建反向 commit，不改历史）：
```bash
git revert <sha>                    # 生成反向 commit
git revert <sha1> <sha2>            # 多个
git revert -n <sha>                 # 不自动 commit
```

**推荐**：已推送的改动用 revert；未推送的可 reset。

### 找回丢失的 commit
```bash
git reflog
# 找到 sha 后
git reset --hard <sha>
# 或恢复为新分支
git branch recover <sha>
```

`reflog` 是本地引用日志，保留 30-90 天。**只要不是 gc 过的都能找回**。

---

## 四、分支策略

### Git Flow（传统）
```
main           长期分支，只有发布版本
develop        集成分支
feature/xxx    功能分支（从 develop 拉）
release/x.y    发布准备
hotfix/xxx     线上紧急修复
```
规范但重、适合版本发布型项目。

### GitHub Flow（互联网常用）
```
main           唯一长期分支，可发布
feature/xxx    从 main 拉，PR 合回 main
```
简单、持续部署。

### GitLab Flow
GitHub Flow + 环境分支（staging、production）。

### Trunk-based Development
所有人在 `main` 或短期分支上开发（1-2 天合入），Feature Flag 控制未完成功能。适合高频发布。

---

## 五、合并策略

### Merge vs Rebase

| 策略 | 历史 | 优缺点 |
|------|------|--------|
| **merge** | 真实保留分支历史 | 易理解但图复杂 |
| **rebase** | 线性历史 | 干净但丢失合并信息 |
| **squash merge** | 一个 commit | 整洁但丢细节 |

### Merge Commit
```bash
git merge --no-ff feature
```
产生合并 commit，图示：
```
* merged feature
|\
| * feature work
| * feature init
|/
* main commit
```

### Rebase 线性
```bash
git switch feature
git rebase main
git switch main
git merge feature  # fast-forward
```

### Squash（推荐合 PR）
```bash
git merge --squash feature
git commit -m "feat: add feature X"
```

GitHub "Squash and merge" 按钮即此。

### 最佳实践
- **feature 分支内部**：可 rebase 整理历史
- **公共分支**（main/develop）：用 merge（**严禁 rebase**）
- **PR 合入**：squash merge（保持 main 整洁）
- **已推送**：用 revert 而非 reset

---

## 六、冲突解决

```bash
git merge feature
# CONFLICT (content): Merge conflict in file.txt
```

文件中标记：
```
<<<<<<< HEAD
main 内容
=======
feature 内容
>>>>>>> feature
```

手动编辑后：
```bash
git add file.txt
git merge --continue  # 或 git commit
```

工具：
```bash
git mergetool  # 启动配置的工具（VSCode、IntelliJ 都支持）
```

---

## 七、Stash

临时保存改动：
```bash
git stash                    # 保存工作区 + 暂存区
git stash -u                 # 包括未跟踪文件
git stash -m "临时保存"      # 带消息

git stash list               # 列表
git stash show               # 查看最近
git stash show -p stash@{0}  # 详细 diff

git stash pop                # 应用并删除
git stash apply stash@{1}    # 应用但保留
git stash drop stash@{0}     # 删除
git stash clear              # 清空
```

---

## 八、子模块与子树

### Submodule（子模块）
引用其他仓库作为子目录。
```bash
git submodule add https://github.com/x/y libs/y
git submodule update --init --recursive
git submodule update --remote
```

### Subtree（子树）
合并另一个仓库，保留历史。
```bash
git subtree add --prefix=libs/y https://github.com/x/y main --squash
git subtree pull --prefix=libs/y https://github.com/x/y main --squash
```

**选择**：submodule 独立但复杂；subtree 易用但仓库变大。实际多用**包管理器**（npm、maven）替代。

---

## 九、Cherry-pick 与 Bisect

### cherry-pick（挑 commit）
```bash
git cherry-pick <sha>             # 把某个 commit 搬到当前分支
git cherry-pick <sha1>..<sha2>    # 一段（不含 sha1）
git cherry-pick -n <sha>          # 不自动 commit
```

用于 hotfix 从 main 回 release，或跨分支同步。

### bisect（二分查找 bug）
```bash
git bisect start
git bisect bad                    # 当前版本有 bug
git bisect good <old-sha>         # 标记好的版本
# Git 自动 checkout 中间 commit
# 测试
git bisect good  # 或 bad
# 重复直到定位到引入 bug 的 commit
git bisect reset
```

或自动化：
```bash
git bisect run ./test.sh
```

---

## 十、标签

```bash
git tag v1.0.0                        # 轻量标签
git tag -a v1.0.0 -m "Release 1.0.0"  # 附注标签
git tag v1.0.0 <sha>                  # 指定 commit

git push origin v1.0.0
git push origin --tags                # 推所有

git tag -d v1.0.0                     # 本地删除
git push -d origin v1.0.0             # 远程删除
```

### 语义化版本
```
MAJOR.MINOR.PATCH
1.0.0 → 1.0.1 (patch，bug 修复)
     → 1.1.0 (minor，新功能，向后兼容)
     → 2.0.0 (major，破坏性更新)
```

---

## 十一、Hooks 钩子

### 客户端 Hooks

`.git/hooks/pre-commit`：
```bash
#!/bin/bash
# 提交前运行测试/lint
npm test || exit 1
npm run lint || exit 1
```

常用：
- `pre-commit`：提交前（lint、test）
- `commit-msg`：校验提交消息格式
- `pre-push`：推送前

### Husky（Node 项目统一管理）

```json
// package.json
{
  "husky": {
    "hooks": {
      "pre-commit": "lint-staged",
      "commit-msg": "commitlint -E HUSKY_GIT_PARAMS"
    }
  }
}
```

### 服务端 Hooks

`pre-receive`、`post-receive` 用于服务端代码审查、CI 触发。

---

## 十二、GitHub / GitLab 协作

### Pull Request / Merge Request

1. Fork 仓库（外部贡献者）
2. 创建 feature 分支
3. 开发、提交、推送
4. PR 到 main
5. Code Review
6. 合并（squash / merge）

### PR 描述

```markdown
## 概述
简述改动

## 变更内容
- 新增 X
- 修复 Y

## 测试
- [ ] 单测覆盖
- [ ] 手动测试 case A
- [ ] 回归测试

## 风险
描述可能影响

## Checklist
- [ ] 无 console.log
- [ ] 文档已更新
```

### Conventional Commits

```
<type>[optional scope]: <description>

[optional body]

[optional footer]
```

类型：
- `feat`：新功能
- `fix`：bug 修复
- `docs`：文档
- `style`：格式
- `refactor`：重构
- `perf`：性能
- `test`：测试
- `chore`：杂项
- `ci`：CI 改动

例：
```
feat(auth): support OAuth2 login

Closes #123
```

### Code Review 要点
- 功能正确
- 代码风格
- 测试覆盖
- 安全（注入、越权）
- 性能（N+1、大循环）
- 可读性
- 文档

---

## 十三、.gitignore

```gitignore
# IDE
.idea/
.vscode/
*.iml

# 构建
target/
dist/
build/
node_modules/

# 语言
*.class
*.pyc
__pycache__/
.venv/

# 日志
*.log
logs/

# 敏感
.env
secrets.yml
*.key
*.pem

# 系统
.DS_Store
Thumbs.db
```

模板：https://github.com/github/gitignore

### 已跟踪文件加入忽略
```bash
git rm --cached file.txt
git commit -m "remove from tracking"
echo "file.txt" >> .gitignore
```

---

## 十四、常见工作流

### 日常开发
```bash
git switch main
git pull
git switch -c feature/new-thing

# 开发...
git add -p
git commit -m "feat: new thing"

# 推送
git push -u origin feature/new-thing
# 开 PR...

# main 更新了，同步
git switch main
git pull
git switch feature/new-thing
git rebase main  # 或 merge

# PR 合入 → 删除本地分支
git switch main
git pull
git branch -d feature/new-thing
```

### 修复冲突 rebase
```bash
git rebase main
# <冲突>
# 编辑冲突文件
git add <resolved files>
git rebase --continue
# 重复直到完成
git push --force-with-lease
```

### Cherry-pick 热修复
```bash
# main 上修复
git switch main
git commit -m "fix: xxx"

# 同步到 release 分支
git switch release/1.0
git cherry-pick <main 的 sha>
git push
```

---

## 十五、进阶技巧

### 清理历史中的大文件
```bash
# git filter-repo（推荐）
pip install git-filter-repo
git filter-repo --path large-file.zip --invert-paths
git push --force
```

### 压缩历史
```bash
git rebase -i --root
# 所有 commit 改成 squash
```

### 查找删除的文件
```bash
git log --all --full-history -- "path/to/file"
```

### 查看两分支公共祖先
```bash
git merge-base main feature
```

### Worktree（多工作目录）
```bash
git worktree add ../feature-wt feature
# 同时在不同目录 checkout 不同分支
```

### GPG 签名
```bash
git config --global user.signingkey <key-id>
git config --global commit.gpgsign true
git commit -S -m "signed commit"
```

---

## 面试高频问题

**Q1：git merge 和 git rebase 区别？**

- **merge**：保留分支真实历史，产生合并 commit，图结构
- **rebase**：把分支 commit 重放到目标分支顶部，历史线性

选择：
- 个人/短期分支整理：rebase
- 合入主分支：squash merge（PR）
- 已推送的公共分支：**不要 rebase**（破坏他人历史）

**Q2：reset 和 revert 区别？**

- **reset**：移动 HEAD 指针，**改变历史**
- **revert**：新建反向 commit，**不改历史**

已推送的改动用 revert（安全）；本地未推送可 reset。

**Q3：误 reset --hard 如何恢复？**

```bash
git reflog           # 找到之前的 HEAD
git reset --hard <sha>  # 或 git cherry-pick、git branch recover <sha>
```

只要未 `git gc`，reflog 保留 30+ 天，commits 可找回。

**Q4：rebase 冲突怎么处理？**

```bash
git rebase main
# 冲突
编辑文件解决冲突
git add <files>
git rebase --continue
# 重复直到完成
```

若想放弃：`git rebase --abort`。

冲突多时，考虑用 merge 代替 rebase。

**Q5：cherry-pick 是什么？**

把某个 commit **复制到**当前分支（产生新 SHA）。
```bash
git cherry-pick <sha>
```

场景：hotfix 从 main 同步到 release、跨分支搬特性。

**Q6：stash 的使用场景？**

临时保存未提交改动：
- 切分支前
- 拉取最新代码前
- 中途要切 bug 修复

```bash
git stash
<切换、拉取...>
git stash pop
```

**Q7：如何撤销一个已推送的 commit？**

```bash
# 方式 1：revert（推荐，不改历史）
git revert <sha>
git push

# 方式 2：reset（改历史，需协调团队）
git reset --hard HEAD~1
git push --force-with-lease
```

公共分支**强烈推荐 revert**。

**Q8：分支策略怎么选？**

- **小团队、持续部署**：GitHub Flow
- **有明确版本发布**：Git Flow
- **有多环境**：GitLab Flow
- **大团队频繁发布**：Trunk-based + Feature Flag

**Q9：如何优化 git log 图？**

```bash
git log --oneline --graph --all --decorate
```

配置别名：
```bash
git config --global alias.lg "log --oneline --graph --all --decorate"
```

GUI：GitHub Desktop、SourceTree、GitKraken、VSCode GitLens。

**Q10：如何处理大仓库？**

- **浅克隆**：`git clone --depth 1`
- **部分克隆**：`git clone --filter=blob:none`（Git 2.19+）
- **稀疏检出**：只 checkout 部分目录
- **LFS**：大文件用 `git lfs`（二进制、媒体）
- **子模块/monorepo 工具**：Nx、Lerna、Bazel

避免：
- 直接提交大二进制（改用 LFS）
- 不必要的构建产物（加 .gitignore）
- 长期不清理的历史（定期 filter-repo）
