# BitTorrent-Tracker-Checker

一个 Python 脚本，用于在本地检查一个BT Tracker是否可用。目前支持udp、http和https协议。

## Usage

目前支持两种 Tracker URL 导入方式：

- 从文件导入
- 从管道导入

默认优先从管道导入。

### 从文件导入

修改文件开头的 `TRACKER_URLS_FILE` 常量为文件路径即可。

### 从管道导入

例子：

```powershell
cat D:\url.txt | python '.\BT Tracker Checker async.py'
```

## 其他

还没做出来 UwU，有错误的话请各位斧正
想保存输出，就用终端管道重定向吧。
