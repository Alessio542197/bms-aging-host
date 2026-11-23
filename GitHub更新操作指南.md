# GitHub 更新操作指南（已上传仓库之后）

适用：仓库已经建好、源码已上传，现在要**改文件**或**发新版本**，让现场的老程序能在线更新。

> 先记住 3 个版本号，后面每一步都围着它们转：
>
> | 版本号 | 在哪里 | 作用 |
> |---|---|---|
> | `APP_VERSION` | `app.py` 顶部（编译进 exe） | 程序自报的版本 |
> | Release 标签 | GitHub 发行版，如 `v2026.11.24.2` | 只用于生成下载地址 |
> | `update.json` 的 `version` | 仓库根目录 | 客户端拿它和 `APP_VERSION` 比较 |
>
> 三者最好保持一致；`update.json` 的 `version` **必须大于**客户端里的 `APP_VERSION`。

---

## 一、只改说明/文档（不发新程序）

不用动 Release，直接改仓库文件即可：

1. 打开仓库 → 点开要改的文件（如 `使用说明.md`）；
2. 右上角 **铅笔图标** → 修改；
3. 页面底部 **Commit changes** → 填一句说明 → 提交。

要替换多个文件/加新文件：

1. 仓库页 **Add file → Upload files**；
2. 把本地文件拖进去（**同名会覆盖**）；
3. **Commit changes**。

> 只改文档不影响现场程序，客户端也不会提示更新。

---

## 二、发新程序版本（完整流程）

### 第 1 步：准备新 EXE

1. 把 `app.py` 顶部的 `APP_VERSION` 改大，例如 `2026.11.24.2` → `2026.11.24.2`；
2. 重新编译出新的 `BmsAgingHost.exe`（没有编译环境就把源码发我，我帮你出 exe 和 SHA256）；
3. 用 `生成SHA256.bat` 或命令算出**新 exe** 的 SHA256：

   ```bat
   certutil -hashfile BmsAgingHost.exe SHA256
   ```

> 注意：一定要算**新 exe** 的哈希，不能沿用上一版的；改文件名不影响哈希。

### 第 2 步：上传新 EXE 到 Release

1. 仓库首页右侧 **Releases** → **Draft a new release**；
2. **Choose a tag** 输入新标签，如 `v2026.11.24.2` → **Create new tag**；
3. **Release title** 填 `v2026.11.24.2`；
4. **Describe this release** 写更新内容（会显示在客户端更新弹窗里）；
5. 把新 `BmsAgingHost.exe` 拖到 **Attach binaries** 区域；
6. 点 **Publish release**（**不要停在 Draft**，草稿的附件下载会 404）；
7. 发布后右键附件 → **复制链接地址**，得到：

   ```
   https://github.com/用户名/仓库名/releases/download/v2026.11.24.2/BmsAgingHost.exe
   ```

### 第 3 步：更新 `update.json`

在仓库里点开 `update.json` → 铅笔 → 改成：

```json
{
  "version": "2026.11.24.2",
  "notes": "1. 新增xxx\n2. 修复xxx",
  "exe_url": "https://github.com/用户名/仓库名/releases/download/v2026.11.24.2/BmsAgingHost.exe",
  "exe_sha256": "第 1 步算出的新哈希"
}
```

四个字段要**同时**改：

| 字段 | 必须 |
|---|---|
| `version` | 大于客户端 `APP_VERSION`，否则客户端认为已是最新 |
| `notes` | 更新说明，可换行写 `\n` |
| `exe_url` | 指向**这次新 Release** 的附件，不能是旧标签 |
| `exe_sha256` | 新 exe 的哈希，旧哈希会导致“校验不通过” |

改完 **Commit changes**。

> GitHub raw 有缓存，提交后一般 1–5 分钟生效；客户端如果刚改完就点检查，可能还拿到旧内容，等几分钟再试。

### 第 4 步：现场验证

1. 在旧版程序点底部 **检查更新**；
2. 应弹出“当前版本 → 最新版本 + 更新内容”；
3. 确认后下载，进度完成会提示“程序将退出并自动重启”；
4. 程序退出后由后台脚本替换 EXE 并重新启动；
5. **记录不会丢**：单文件版记录在 `%LOCALAPPDATA%\BmsAgingHost\记录`，升级不动它。

也可以先命令行自检清单：

```bat
BmsAgingHost.exe --check-update https://raw.githubusercontent.com/用户名/仓库名/main/update.json
```

---

## 三、只改仓库里的 `config.json` 会怎样

- 仓库里的 `config.json` 只是**新安装用户的默认配置**；
- 已经运行过的现场程序用的是 `%LOCALAPPDATA%\BmsAgingHost\config.json`，**不会被仓库文件覆盖**；
- 想让现场改默认值，要么让用户在界面/配置文件里改，要么在新版本代码里改默认值（`DEFAULT_CONFIG`）。

---

## 四、常见错误对照

| 现象 | 原因 | 处理 |
|---|---|---|
| 点检查更新提示“已是最新” | `update.json` 的 `version` 没大于客户端版本 | 改大版本号并提交 |
| 还是旧内容/旧版本 | `update.json` 没提交成功，或 raw 缓存 | 看仓库文件确认；等几分钟 |
| 下载 404 | `exe_url` 标签/文件名不对，或 Release 还是 Draft | 对照 Release 页面链接，重新 Publish |
| 下载的是旧程序 | `exe_url` 还指向上一个 Release | 改成新标签的链接 |
| “SHA256 校验不通过” | 哈希不是这次上传的 exe 的 | 重新对新 exe 算一次 |
| 提示“无法解析域名” | 地址写错/用了被屏蔽的域名 | 换 GitHub Release 直链、Gitee 或公司服务器 |
| 私有仓库下载失败 | raw/附件不能匿名访问 | 仓库改 Public，或把文件放公司服务器 |
| 改完代码但 exe 没变 | 只提交了源码，没重新编译上传 | 重新编译并走第 2、3 步 |

---

## 五、回滚到旧版本

客户端只认“版本号更大”的更新，不能直接降级。要回滚：

1. 把旧版 exe 作为附件，发一个新的 Release（标签比当前更大，例如 `v2026.11.25.1`）；
2. `update.json` 的 `version` 也写这个更大的版本号，`exe_url` 指向旧 exe，`exe_sha256` 用旧 exe 的哈希；
3. 客户端更新后会变成旧功能、但版本号更高的程序。

---

## 六、发版检查清单（可打印）

- [ ] `app.py` 的 `APP_VERSION` 已改大
- [ ] 重新编译出新的 `BmsAgingHost.exe`
- [ ] 新 exe 的 SHA256 已生成
- [ ] GitHub Release 已 **Publish**（不是 Draft）
- [ ] `update.json` 的 version / notes / exe_url / exe_sha256 四项都改了
- [ ] raw 地址打开能看到新 version
- [ ] 旧版客户端点“检查更新”能下载并自动重启
- [ ] 更新后记录仍在 `%LOCALAPPDATA%\BmsAgingHost\记录`
