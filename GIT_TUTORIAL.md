# Git 小白入门教程

> 本文用最简单的语言教你把代码保存到 Git，就像保存 Word 文档一样。

---

## 1. Git 是什么？

Git 就是一个**代码的时光机**。

它能帮你：
- 保存代码的每一个版本（改错了可以回退）
- 把代码同步到云端（换电脑也能下载）
- 多人协作不冲突

---

## 2. 核心概念（记住这 3 个）

| 概念 | 比喻 | 命令 |
|---|---|---|
| **工作区** | 你正在编辑的文件夹 | 平时写代码的地方 |
| **暂存区** | 准备保存的清单 | `git add` |
| **仓库** | 已经保存的快照 | `git commit` |

**远程仓库**：存在 GitHub 上的备份，用 `git push` 传上去。

---

## 3. 最常用命令（就这 4 个）

### 3.1 查看修改了哪些文件

```bash
git status
```

**红色** = 还没保存  
**绿色** = 已经放进暂存区，准备保存

### 3.2 把修改添加到暂存区

```bash
git add -A
```

`-A` 表示"所有修改过的文件都加进来"。

只想加某个文件：

```bash
git add 文件名.py
```

### 3.3 保存（提交）

```bash
git commit -m "这里写你做了什么修改"
```

例如：

```bash
git commit -m "修复了 Docker 路径问题"
```

### 3.4 推送到远程仓库（GitHub）

```bash
git push origin dev3
```

`dev3` 是当前分支名，推上去后你的队友就能看到修改了。

---

## 4. 完整操作流程（每次改完代码都做一遍）

```bash
# 1. 进入项目目录
cd /Users/caramel/kddcup2026-data-agents-starter-kit

# 2. 看看改了哪些文件
git status

# 3. 把所有修改加入暂存区
git add -A

# 4. 保存，写上修改说明
git commit -m "修改了 xxx"

# 5. 推送到 GitHub
git push origin dev3
```

---

## 5. 查看历史记录

### 5.1 查看提交历史

```bash
git log
```

按 `q` 退出。

### 5.2 只看最近的 5 条

```bash
git log --oneline -5
```

### 5.3 查看某次提交改了什么

```bash
git show 提交编号
```

提交编号就是 `git log` 里那一串字母数字，比如 `50cd46b`。

---

## 6. 后悔药（回退操作）

### 6.1 改错了，想回到上一次保存的状态

```bash
git checkout -- 文件名.py
```

### 6.2 已经 `git add` 了，但还没 commit，想撤回

```bash
git reset HEAD 文件名.py
```

### 6.3 已经 commit 了，想撤销最后一次提交

```bash
git reset --soft HEAD~1
```

---

## 7. 常见问题

### Q: `git push` 提示 "Everything up-to-date"

说明你这次没有新的修改，或者忘记 `git commit` 了。

### Q: `git push` 提示 "rejected"

可能是远程有别人推送了新代码，先拉取再推送：

```bash
git pull origin dev3
git push origin dev3
```

### Q: 提交信息写错了，想修改

```bash
git commit --amend -m "新的提交信息"
```

### Q: 怎么知道当前在哪个分支？

```bash
git branch
```

前面带 `*` 的就是当前分支。

---

## 8. 本项目相关

- **远程仓库**：`https://github.com/yanjip/kddcup2026-data-agents-starter-kit.git`
- **当前分支**：`dev3`
- **已排除的文件**（不会提交）：`public/`、`score/`、`*.tar.gz`、`.venv/`、`artifacts/`

---

## 9. 总结

| 场景 | 命令 |
|---|---|
| 查看改了什么 | `git status` |
| 准备保存 | `git add -A` |
| 保存 | `git commit -m "说明"` |
| 上传到 GitHub | `git push origin dev3` |
| 查看历史 | `git log --oneline` |

记住口诀：** status → add → commit → push **
