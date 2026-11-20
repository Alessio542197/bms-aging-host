# GitHub 上传与在线更新说明书

适用对象：把「电池老化上位机」放到 GitHub，并用 GitHub Releases 分发 EXE、用仓库里的
`update.json` 做在线更新。全流程不需要写代码，照着点即可。

> **最简 5 步**
> 1. 注册/登录 GitHub → New repository 建一个公开仓库；
> 2. Add file → Upload files，把本文件夹里的源码/文档拖上去（**不要**拖 `runtime`、exe、记录）；
> 3. Releases → Create a new release，打 tag `v2026.11.20.1`，把 EXE 作为附件上传；
> 4. 复制 EXE 下载链接，填进 `update.json`，提交；
> 5. 把 `https://raw.githubusercontent.com/用户名/仓库名/main/update.json` 填到程序 `config.json` 的
>    `update.manifest_url`，重启程序点“检查更新”。

---

## 0. 先分清两个地方

| 位置 | 放什么 | 大小限制 |
|---|---|---|
| **仓库 Repository** | 源码、说明、截图、`update.json` 这类小文件 | 单文件 ≤100 MB，仓库建议 <1 GB |
| **发行版 Releases** | `BmsAgingHost.exe`、便携版 zip 这类大文件 | 单个附件 ≤2 GB |

**不要把 `runtime\`（约 37 MB）和 exe 提交进仓库**：源码仓库保持轻量，大文件走 Releases，
下载地址也更稳定。

---

## 1. 准备工作

- 一个 GitHub 账号（https://github.com/signup，邮箱验证即可）；
- 本文件夹 `电池老化上位机_GitHub发布`，里面已经准备好：
  - `README.md`：仓库首页介绍（含截图）
  - `.gitignore`：告诉 git 忽略 runtime、exe、记录、缓存
  - 源码：`app.py`、`bms_protocol.py`、`win_serial.py`、`tray_icon.py`、`updater.py`
  - 文档：`使用说明.md`、本说明书、`界面截图.png`
  - 配置：`config.json`（模板）、`update.json`（更新清单模板）、`update_manifest.example.json`
  - 启动脚本：4 个 `.bat`
  - 测试：`tests\`、`build\Program.cs`

---

## 2. 三种上传方式怎么选

| 方式 | 适合 | 需要安装 | 大文件 |
|---|---|---|---|
| **网页上传** | 第一次、文件不多 | 不用 | 单文件 ≤25 MB |
| **GitHub Desktop** | 以后经常改代码 | 装一个客户端 | 走 git，单文件 ≤100 MB |
| **命令行 git** | 熟悉 git | 装 Git | 同上 |

第一次建议用**网页上传**；如果以后要经常改，再装 GitHub Desktop。

---

## 3. 方式一：网页上传（推荐第一次用）

### 3.1 建仓库

1. 打开 https://github.com 并登录；
2. 右上角 **+** → **New repository**；
3. 填写：
   - **Repository name**：`bms-aging-host`（英文即可，后面 URL 会用到）
   - **Description**：`电池老化测试上位机（6S BMS）`
   - 选 **Public**（公开）
   - **不要**勾 “Add a README file”，我们已经有了
4. 点 **Create repository**。

### 3.2 上传文件

1. 在仓库页面点 **Add file** → **Upload files**；
2. 打开本文件夹，选中下面这些**文件和文件夹**，拖进网页的上传区域：

   ```
   app.py  bms_protocol.py  win_serial.py  tray_icon.py  updater.py
   config.json  app.ico  update.json  update_manifest.example.json
   README.md  使用说明.md  GitHub发布说明书.md  界面截图.png
   GitHub上传说明书.docx  GitHub上传说明书.pdf
   启动上位机.bat  后台运行.bat  模拟演示.bat  检查串口.bat
   tests\        build\
   .gitignore
   ```

   > `.gitignore` 是隐藏文件：资源管理器 → 查看 → 勾选“隐藏的项目”才能看到。
   > 网页上传**不会**读取 `.gitignore`，拖什么就传什么，所以千万别把
   > `runtime\`、`电池老化上位机.exe`、`记录\` 拖进去。

3. 页面底部 **Commit changes**：
   - 提交说明填 `first commit`
   - 点 **Commit changes**。

4. 上传完成后，仓库首页应能直接看到 README 和界面截图。

### 3.3 确认分支名

默认分支一般是 `main`。仓库首页左上角分支下拉框会显示；后面 raw 地址要用这个分支名。

---

## 4. 发布 Release（放 EXE）

Release 就是“版本发布页”，每个版本一个固定下载地址。

1. 仓库首页右侧点 **Releases** → **Create a new release**；
2. **Choose a tag** 输入 `v2026.11.20.1` → 点 **Create new tag**；
3. **Release title** 填 `v2026.11.20.1`；
4. **Describe this release** 填更新内容（可以复制 `update.json` 里的 notes）；
5. 把 `电池老化上位机.exe` 拖到 **Attach binaries by dropping them here** 区域；
   - 建议先把文件名改成英文 `BmsAgingHost.exe`，下载 URL 更干净；
   - 也可以同时上传便携版 `BmsAgingHost_portable.zip`；
6. 点 **Publish release**；
7. 在发布页找到 `BmsAgingHost.exe`，右键 → **复制链接地址**，得到类似：

   ```
   https://github.com/你的用户名/bms-aging-host/releases/download/v2026.11.20.1/BmsAgingHost.exe
   ```

这个链接就是 `update.json` 里的 `exe_url`。

---

## 5. 配置在线更新

### 5.1 修改 update.json

打开本地的 `update.json`，填上版本、说明、下载链接和 SHA256：

```json
{
  "version": "2026.11.21.1",
  "notes": "1. 新增均衡状态显示\n2. 窗口可自由缩放",
  "exe_url": "https://github.com/你的用户名/bms-aging-host/releases/download/v2026.11.21.1/BmsAgingHost.exe",
  "exe_sha256": "新 EXE 的 SHA256"
}
```

- `version` 必须**大于程序内置版本**（当前是 `2026.11.20.1`）才会提示更新；
- `exe_url` 填第 4 步复制的链接；
- SHA256 生成命令（在 exe 所在目录打开 cmd）：

  ```bat
  certutil -hashfile BmsAgingHost.exe SHA256
  ```

  把输出的十六进制串去掉空格填入即可。

> **第一次上传的注意**：如果还没准备好新 EXE，先把 `update.json` 的 `version` 填成当前版本
> `2026.11.20.1`（或干脆先不提交这个文件），否则旧版程序会提示“发现新版本”但链接是占位地址，
> 下载会 404。等真正发布新 EXE 时再把 version 改大。

### 5.2 提交 update.json

- 网页：仓库里点开 `update.json` → 右上角铅笔图标 → 粘贴新内容 → **Commit changes**；
- Desktop：本地改完 → 写 commit → **Push origin**。

### 5.3 得到更新清单地址

公开仓库直接用 raw 地址：

```
https://raw.githubusercontent.com/你的用户名/bms-aging-host/main/update.json
```

> 如果默认分支不是 `main`，把 URL 里的 `main` 换成实际分支名。

也可以启用 GitHub Pages（可选）：

1. 仓库 **Settings** → **Pages**；
2. **Source** 选 `Deploy from a branch`，分支 `main`、目录 `/(root)`，**Save**；
3. 等 1–2 分钟，清单地址变成：

   ```
   https://你的用户名.github.io/bms-aging-host/update.json
   ```

### 5.4 填到程序里

程序目录（单文件版解压在 `%LOCALAPPDATA%\BmsAgingHost\app`）的 `config.json`：

```json
"update": {
  "manifest_url": "https://raw.githubusercontent.com/你的用户名/bms-aging-host/main/update.json",
  "timeout_s": 15
}
```

保存后重启程序，点底部 **检查更新**。

### 5.5 先自检（不下载）

```bat
BmsAgingHost.exe --check-update https://raw.githubusercontent.com/你的用户名/bms-aging-host/main/update.json
```

输出里出现 `newer=True` 就说明清单地址和版本号都正确。

---

## 6. 日常维护

### 6.1 改代码

- **网页**：打开文件 → 铅笔 → 修改 → Commit changes；
- **Desktop**：本地修改 → 左侧勾选文件 → 写说明 → **Commit to main** → **Push origin**。

### 6.2 发布新版本（完整清单）

1. 修改 `app.py` 顶部的 `APP_VERSION` 和 `build/Program.cs` 的 `Version`（两处要一致）；
2. 重新编译出新的 exe；
3. `certutil -hashfile` 生成 SHA256；
4. GitHub → Releases → **Draft a new release**：
   - tag 比上一版大，例如 `v2026.11.21.1`
   - 上传新 exe（文件名建议保持 `BmsAgingHost.exe`）
   - Publish release
5. 更新并提交 `update.json`：version / notes / exe_url / exe_sha256；
6. 打开旧版程序点“检查更新”，确认能下载、校验、自动重启。

版本号规则：按数字段比较，任何一段变大即可，例如：

```
2026.11.21.1  >  2026.11.20.1  >  2026.11.19.4
```

### 6.3 私有仓库怎么办

- 私有仓库的 `raw.githubusercontent.com` 和 Release 下载链接**不能匿名访问**，
  程序会下载失败（404/401）；
- 两个办法：
  1. 仓库设为 **Public**（源码会公开）；
  2. 把 `update.json` 和 exe 放到公司服务器/对象存储等公开地址，程序只认 HTTP(S) URL。
- **不要**把 GitHub Token 写进 URL 或 `update.json`，会泄露。

### 6.4 仓库大小注意

- 单文件超过 100 MB 会被 GitHub 拒绝；超过 50 MB 会有警告；
- `runtime\`（37 MB）和 exe 不要提交，走 Releases；
- `记录\*.csv`、`__pycache__\` 已在 `.gitignore` 排除，也不要在网页上手动拖进去。

---

## 7. 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| 网页上传按钮灰色或失败 | 单文件 >25 MB 或一次文件太多 → 分批传，或改用 GitHub Desktop |
| README 里的截图不显示 | 图片文件名/大小写与 `README.md` 里的路径不一致；确认 `界面截图.png` 在根目录 |
| raw 地址 404 | 分支名不是 `main`、文件不在根目录、仓库是私有 |
| 点“检查更新”提示“已是最新” | `update.json` 的 `version` 没有大于程序内置版本 |
| 下载后没有自动重启 | 文件夹版只提示手动替换；只有单文件 EXE 支持自动替换 |
| 更新脚本一闪而过 | 正常，它在后台等待旧进程退出、覆盖 exe、再启动 |
| 杀毒软件拦截新 EXE | 首次运行会扫描，加入信任/白名单即可 |
| 中文文件名下载失败 | Release 附件尽量用英文名（`BmsAgingHost.exe`），中文会做 URL 编码 |
| GitHub 网页很慢/打不开 | 用 GitHub Desktop 或公司网络代理；也可以先传到 Gitee 再同步 |

---

## 8. 安全与规范

- 公开仓库 = 源码公开，提交前确认没有客户数据、串口密码、Token；
- `config.json` 里目前只有串口/阈值，没有敏感信息；
- 更新清单建议用 HTTPS；`exe_sha256` 必须填，程序会拒绝校验不通过的文件；
- 记录文件（`记录\`）包含现场测试数据，不要提交；
- 建议在 README 里写清“仅供内部测试使用”，避免误用。

---

## 附：本文件夹内容清单

```
README.md                  仓库首页介绍
.gitignore                 忽略 runtime/exe/记录/缓存
app.py                     主程序
bms_protocol.py            协议与解码
win_serial.py              Win32 串口
tray_icon.py               托盘
updater.py                 在线更新
config.json                配置模板
app.ico                    图标
update.json                在线更新清单（需要你填链接和 SHA256）
update_manifest.example.json  清单字段示例
使用说明.md                 使用手册
GitHub发布说明书.md          本文件
界面截图.png                界面截图
启动上位机.bat 等 4 个      免安装启动脚本
tests\                     回归测试
build\Program.cs           单文件 EXE 启动器源码
```
