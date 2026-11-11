# 将 QuotaPill 发布到 GitHub（仓库维护者）

GitHub 仓库名称建议为 **lbg2002/quota-pill**，公开（Public），默认分支 `main`。

本目录已包含源码、README（中英文）、LICENSE、三张截图、单元测试和 GitHub Actions。**发布前建议在实际 GNOME + XWayland 桌面上再进行安装与交互测试**；静态检查通过不等于真实桌面兼容性已经验证。

## 使用 GitHub CLI 一次创建并推送

Ubuntu 若未安装 `gh`：

```bash
sudo apt update && sudo apt install gh
```

然后在已解压的 `quota-pill` 项目根目录：

```bash
gh auth login -w     # 使用浏览器登录 GitHub；兼容双因素验证
git init -b main
git add .
git commit -m "feat: initial QuotaPill Linux widget release"
gh repo create lbg2002/quota-pill --public \
  --description "Lightweight GTK3 AI quota ring widget for Antigravity and Codex" \
  --source=. --remote=origin --push
```

如果已经是 Git 仓库，跳过 `git init`，并检查 `git remote -v`；如仓库已经存在，不要无条件覆盖它。

成功后确认：

- 打开 `https://github.com/lbg2002/quota-pill`，首页 README 和图片均正常显示。
- GitHub Actions 的 **Static checks** 完成。
- 在自己的 Ubuntu/GNOME 环境中执行 `./install.sh --no-start`，检查可执行文件和启动项，再手动运行挂件。
- 确认 `codexbar usage --provider codex --format json` 与 `codexbar usage --provider antigravity --no-color` 均能返回额度。
- 确认没有任何 Token、Cookie、个人认证文件被提交。

## 建议首次发布信息

Tag: `v0.1.0`

Title: `QuotaPill v0.1.0 — Linux AI Quota Widget`

Release notes: Lightweight GTK3/Cairo quota ring widget for Codex and Antigravity with hover details, customizable ring colors/layouts, safe CLI installer, offline cache, and bilingual docs. The initial release targets Ubuntu/Debian on X11 or XWayland; other desktop environments are not yet verified.
