# qq-nt-export

把电脑版 QQ NT（9.9.x）的本地聊天记录解密，并导出成可读的 txt。全程在本地运行，不上传任何数据。

> 只用于导出**你自己账号**的聊天记录。

## 文件

| 文件 | 作用 |
|------|------|
| `decrypt_qq.py` | 用 16 位口令解密 `nt_msg.db`，输出明文 SQLite |
| `qq_export.py` | 从明文库导出每个会话的 txt，外加一份"我发的所有消息" |

密钥抓取和消息正文解析依赖 [chatlog-keeper](https://github.com/labazhou2024/chatlog-keeper)。

## 准备

- Windows，已安装 QQ NT 9.9.x
- Python 3.9+

```bash
git clone https://github.com/labazhou2024/chatlog-keeper
pip install -r chatlog-keeper/requirements.txt
pip install pycryptodome numpy zstandard
```

运行 `qq_export.py` 前，要能 `import chatlog_keeper`：在 chatlog-keeper 目录里运行 `pip install -e .`，或者把它所在目录加进 `PYTHONPATH`。

## 第 1 步：找到数据库

默认位置：

```
C:\Users\<用户名>\Documents\Tencent Files\<QQ号>\nt_qq\nt_db\nt_msg.db
```

不在默认位置的话，可以搜一下：

```powershell
Get-ChildItem C:\,D:\,E:\ -Recurse -Depth 5 -Filter "nt_msg.db" -ErrorAction SilentlyContinue
```

## 第 2 步：获取密钥

数据库用 SQLCipher 加密，密钥是一个 16 位 ASCII 口令，只存在 QQ 进程的内存里。

先试被动扫描（不需要管理员权限，但 QQ 9.9.31 以后大多扫不到）：

```bash
python -m chatlog_keeper.cli extract-key --source qq --method passive
```

失败就改用主动模式。用**管理员** PowerShell 运行：

```powershell
# 如果数据目录不在默认位置
$env:CHATLOG_QQ_DATA_ROOT = "<Tencent Files 所在目录>"
python -m chatlog_keeper.cli extract-key --source qq --method active
```

这会弹出一个新的 QQ 窗口，正常登录即可。看到 `"ok": true, "key_len": 16` 就说明成功了，密钥会缓存到 `%LOCALAPPDATA%\chatlog-keeper\data\secrets\qq_db.key`。

⚠️ 主动模式会给 QQ 进程挂调试断点，有一定封号风险，chatlog-keeper 自己也有提示。

⚠️ 拿到密钥后，要**正常退出 QQ**（托盘右键 → 退出），让 WAL 日志合并回主库。强行结束进程会留下坏页。

## 第 3 步：解密

```bash
python decrypt_qq.py --db "<nt_msg.db 路径>" --key-file "%LOCALAPPDATA%\chatlog-keeper\data\secrets\qq_db.key" --out decrypted.db
```

能打印出 `c2c_msg_table` 和 `group_msg_table` 的行数，就说明解密成功。明文库可能有好几个 GB。

## 第 4 步：导出 txt

```bash
python qq_export.py --db decrypted.db --out ./qq_out --me <你的QQ号>
```

输出：

```
qq_out/
  ├── 私聊_<好友QQ号>.txt
  ├── 群聊_<群号>.txt
  └── _我发的所有消息.txt
```

群聊默认只导出你自己的发言，这样快很多。要导出全部消息，加 `--groups all`（群消息可能有上千万条，会很慢）。

每个 txt 的格式和常见的微信导出一致：

```
2024-01-01 12:00:00 '发送者'
消息内容
```

## 常见问题

**`database disk image is malformed`**
- QQ 被强行结束，WAL 没合并：重新打开 QQ，再正常退出，然后重新解密
- 数据库真的有坏页：`qq_export.py` 按 rowid 分批读表，遇到坏页时只跳过坏掉的那一小段，其余部分照常读完，并在结束时打印被跳过的 rowid 区间
- 解密参数不对：QQ 9.9.x 的参数是 KDF=SHA-512 4000 轮、HMAC=SHA-1、reserve=48、page_size=4096

**被动扫描超时**：QQ 9.9.31 以后直接用 active 模式。

## 隐私

解密后的数据库、密钥文件和导出的 txt 都是你的私人数据，**不要提交到任何仓库**。本仓库的 `.gitignore` 已经排除了 `*.db`、`*.key`、`*.txt`、`*.json` 和 `*.html`。
