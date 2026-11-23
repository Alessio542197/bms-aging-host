# Gitee（码云）上传与在线更新说明书

适用：把「电池老化上位机」放到 Gitee，用 Gitee 的 **发行版** 放 EXE，用仓库里的
`update.json` 做在线更新。国内访问比 GitHub 稳定，`raw.githubusercontent.com` 打不开时推荐用这个。

> 最简 5 步：
> 1. 登录 gitee.com → 右上角 **+ → 新建仓库**；
> 2. 仓库页 **上传文件**，把源码/文档拖上去（不要传 runtime、exe、记录）；
> 3. **发行版 → 创建发行版**，标签 `v2026.11.24.2`，上传 `BmsAgingHost.exe`；
> 4. 改 `update.json`：版本、`exe_url`、`exe_sha256`，提交；
> 5. 把清单地址（`https://gitee.com/用户名/仓库名/raw/master/update.json`）
>    填到程序 `config.json` 的 `update.manifest_url`，点“检查更新”。

---

## 1. 注册与准备

1. 打开 https://gitee.com ，用手机号注册/登录；
2. 按页面提示完成**实名认证**（Gitee 创建公开仓库、发行版可能要求实名）；
3. 准备好本文件夹 `电池老化上位机_GitHub发布`（里面的源码和文档同样适用于 Gitee）。

## 2. 新建仓库

1. 右上角 **+** → **新建仓库**；
2. 仓库名称：`bms-aging-host`（英文）；
3. 仓库介绍：`电池老化测试上位机（6S BMS）`；
4. 选择 **开源**（公开）；
5. **不要**勾“使用 Readme 文件初始化仓库”（我们有自己的 README）；
6. 点 **创建**。

> 创建后注意默认分支：Gitee 默认通常是 `master`，也可能是 `main`。
> 仓库首页左上角的分支下拉框能看到，后面 raw 地址要用这个分支名。

## 3. 上传源码

1. 进入仓库，点 **上传文件**（或“添加文件 → 上传文件”）；
2. 把下面这些拖进上传区域：

   ```
   app.py  bms_protocol.py  win_serial.py  updater.py
   config.json  app.ico  update.json  update_manifest.example.json
   README.md  使用说明.md  GitHub发布说明书.md  界面截图.png
   启动上位机.bat  模拟演示.bat  检查串口.bat
   生成SHA256.bat  tests\  build\
   ```

3. 填写提交信息（如 `first commit`），点 **提交**。

> 网页上传有单文件大小限制，源码都很小没问题；
> **不要**把 `runtime\`（37 MB）、`电池老化上位机.exe`、`记录\` 拖进仓库。
> 大文件走“发行版”。

## 4. 创建发行版（放 EXE）

1. 仓库页面顶部点 **发行版**（Releases）→ **创建发行版**；
2. 版本号填 `v2026.11.24.2`（标签）；
3. 发行版标题：`v2026.11.24.2`；
4. 描述填更新内容；
5. 在附件区域上传 `BmsAgingHost.exe`（建议先把 exe 改名成英文）；
   也可以同时上传便携版 zip；
6. 点 **发布**。

发布后在发行版页面点附件即可下载，复制下载链接，形如：

```
https://gitee.com/你的用户名/bms-aging-host/releases/download/v2026.11.24.2/BmsAgingHost.exe
```

这个地址就是 `update.json` 里的 `exe_url`。

## 5. 取更新清单地址

打开仓库里的 `update.json`，点 **原始数据**（Raw），浏览器地址栏就是：

```
https://gitee.com/你的用户名/bms-aging-host/raw/master/update.json
```

- 默认分支是 `main` 就把 `master` 换成 `main`；
- 这个地址要能**不登录直接打开**，否则程序下载会失败（仓库必须是公开的）。

## 6. 填写 update.json

```json
{
  "version": "2026.11.24.2",
  "notes": "更新说明",
  "exe_url": "https://gitee.com/你的用户名/bms-aging-host/releases/download/v2026.11.24.2/BmsAgingHost.exe",
  "exe_sha256": "新 EXE 的 SHA256"
}
```

- `version` 必须大于程序内置版本（当前 `2026.11.24.2`）；
- SHA256 用 `生成SHA256.bat` 双击生成，或：

  ```bat
  certutil -hashfile BmsAgingHost.exe SHA256
  ```

- 第一次上传时如果还没有新 exe，`version` 先填当前版本 `2026.11.24.2`，避免旧版提示更新但链接 404。

提交 `update.json` 后，raw 地址内容会自动更新（可能有几分钟缓存）。

## 7. 配置程序

单文件版配置文件在 `%LOCALAPPDATA%\BmsAgingHost\config.json`；
文件夹版在程序目录的 `config.json`。

```json
"update": {
  "manifest_url": "https://gitee.com/你的用户名/bms-aging-host/raw/master/update.json",
  "timeout_s": 15,
  "proxy": ""
}
```

保存后重启程序，点底部 **检查更新**。

## 8. 自检

```bat
电池老化上位机.exe --check-update https://gitee.com/你的用户名/bms-aging-host/raw/master/update.json
```

会弹出命令行窗口显示清单内容；最后出现 `newer=True` 就说明地址、版本号都正确。

## 9. 日常更新流程

1. 改代码，重新编译 exe；
2. 生成新 exe 的 SHA256；
3. Gitee → 发行版 → 创建发行版（新标签，如 `v2026.11.24.2`），上传新 exe；
4. 修改 `update.json` 的 version / notes / exe_url / exe_sha256，提交；
5. 打开旧版程序点“检查更新”验证。

## 10. 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| 11004 / GETADDRINFO FAILED | 用了 `raw.githubusercontent.com`，改用 Gitee raw 地址 |
| raw 地址返回登录页/HTML | 仓库是私有的；改成公开仓库，或把清单放公司服务器 |
| 404 | 分支写错（`master`/`main`）、文件路径不对、仓库私有 |
| 发行版附件下载 403 | 附件可能要求登录；改用公开仓库或公司服务器托管 exe |
| 网页上传失败 | 文件超过网页上传限制；大文件走发行版 |
| 提示“已是最新” | `update.json` 的 version 没有大于程序内置版本 |
| 更新后记录丢了 | 本版本已把记录放在 `%LOCALAPPDATA%\BmsAgingHost\记录`，不应丢失；确认没有手动删除 |
| 上传后 raw 内容没变 | Gitee 有缓存，等几分钟或强制刷新 |

## 11. 大小与限制（以 Gitee 页面提示为准）

- 仓库不建议放大文件；`runtime`、exe 用发行版；
- 发行版附件有单文件大小限制，14 MB 的 exe 一般可上传；
- 若附件限制导致传不上，把 exe 放到公司服务器/对象存储，`exe_url` 指向那里即可；
- 公开仓库的源码是公开的，提交前确认没有敏感信息。

## 附：最小可用清单

仓库根目录只需要这些就能工作：

```
app.py  bms_protocol.py  win_serial.py  updater.py
config.json  app.ico  update.json
README.md  使用说明.md
```

`runtime`、exe、便携版 zip 放发行版；`tests`、`build` 可选。
