# 第 7 章证据：精确编辑

## 三条规则

| 情形 | 行为 | 理由 |
|---|---|---|
| `old` 出现 0 次 | 报"not found"，提示逐字复制含缩进 | 猜一个近似位置比失败更糟 |
| `old` 出现 N>1 次 | 报"appears N times (lines [...])"，拒绝执行 | 让模型补上下文或显式 `all=true` |
| `old` == `new` | 报"identical"，拒绝执行 | 空操作说明模型搞错了对象 |

## 只改目标，不重写全文

实现上用字符串切片替换，不经过"读全文 → 改 → 整体重写"，所以：

- 换行风格：**CRLF 保持 CRLF**；
- 结尾没有换行：保持没有换行；
- 缩进、行尾空白、空行数量：原样不动。

## 实测：改一行要写多少字节

`experiments/edit_metrics.py`，同一份 2406 行 / 42,286 字节的文件，同一处改动：

| 方式 | 写入字节 | 报告的行变更数 |
|---|---|---|
| `edit_file` | **11** | **1** |
| `write_file`（整篇重写） | 42,297 | 2,406 |

比值 **3845×**。两者最终文件内容完全相同（`content_equal: true`），
差别只在"模型要输出多少"和"文件被动过多少"。

这条指标直接对应一个真实风险：整篇重写时，模型只要在任何一处不小心
（丢掉一个空行、把 tab 换成空格、把 CRLF 换成 LF），diff 里就会出现
几百行无关变更，review 时无法判断真正改了什么。

## 踩到的坑：LF 的 `old` 匹配 CRLF 的文件

第一版实现直接 `original.count(old)`，在 Windows 上**全部失败**：

```
`old` text not found in e.py. Copy the exact text including indentation; do not retype it.
```

原因是文件在磁盘上是 `\r\n`，而模型给的 `old` 是 `\n`。这个 bug 的迷惑性在于
报错文本完全是"你没抄对"，会诱导模型反复重试同一段文本。

修法是按文件的换行风格归一化后匹配，写回时再还原：

```python
raw = target.read_bytes()
crlf = b"\r\n" in raw
original = raw.decode("utf-8")
haystack = original.replace("\r\n", "\n") if crlf else original
needle = old.replace("\r\n", "\n")
...
updated = haystack.replace(needle, replacement, ...)
if crlf:
    updated = updated.replace("\n", "\r\n")
target.write_bytes(updated.encode("utf-8"))
```

配套测试 `test_lf_needle_matches_a_crlf_file` 锁住这个行为。

## 测试覆盖

`tests/test_edit.py` 共 12 条，每条对应一种真实会犯的错：
唯一匹配、重复匹配给行号、`all=true`、找不到、`old==new`、CRLF 保留、
LF 匹配 CRLF、结尾无换行、行变更计数、未触及区域格式不变、越界路径、
大文件小改动。
